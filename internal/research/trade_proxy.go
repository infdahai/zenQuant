package research

import (
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
)

const proxyFourHourNS int64 = 4 * 3600 * 1_000_000_000

// RecentTrade is an offline synthetic representation of a public recent-trade row.
type RecentTrade struct {
	Side    string  `json:"side"`
	Price   string  `json:"px"`
	Size    string  `json:"sz"`
	TimeMS  int64   `json:"time"`
	TradeID int64   `json:"tid"`
	Hash    *string `json:"hash"`
}

// UnmarshalJSON requires all trade fields except hash; zero is a valid tid.
// Value validation remains the responsibility of NormalizeRecentTrades.
func (r *RecentTrade) UnmarshalJSON(data []byte) error {
	var raw struct {
		Side    *string `json:"side"`
		Price   *string `json:"px"`
		Size    *string `json:"sz"`
		TimeMS  *int64  `json:"time"`
		TradeID *int64  `json:"tid"`
		Hash    *string `json:"hash"`
	}
	if err := json.Unmarshal(data, &raw); err != nil {
		return err
	}
	if raw.Side == nil || raw.Price == nil || raw.Size == nil || raw.TimeMS == nil || raw.TradeID == nil {
		return fmt.Errorf("recent trade requires non-null side, px, sz, time and tid")
	}
	*r = RecentTrade{Side: *raw.Side, Price: *raw.Price, Size: *raw.Size, TimeMS: *raw.TimeMS, TradeID: *raw.TradeID, Hash: raw.Hash}
	return nil
}

type ProxyTrade struct {
	Symbol      string  `json:"symbol"`
	Side        string  `json:"side"`
	Price       float64 `json:"price"`
	Size        float64 `json:"size"`
	TimeMS      int64   `json:"ts_ms"`
	TradeID     int64   `json:"trade_id"`
	NotionalUSD float64 `json:"notional_usd"`
	Hash        *string `json:"hash"`
	Source      string  `json:"source"`
}

type TradeProxyBucket struct {
	StartNS    int64   `json:"bucket_start_ns"`
	EndNS      int64   `json:"bucket_end_ns"`
	Symbol     string  `json:"symbol"`
	LongCount  int     `json:"long_liq_count"`
	ShortCount int     `json:"short_liq_count"`
	LongUSD    float64 `json:"long_liq_usd"`
	ShortUSD   float64 `json:"short_liq_usd"`
	TotalUSD   float64 `json:"total_liq_usd"`
	MaxUSD     float64 `json:"max_single_liq_usd"`
	Imbalance  float64 `json:"liq_imbalance"`
	Source     string  `json:"source"`
}

// NormalizeRecentTrades preserves input order and duplicates. TimeMS is event
// time, not receipt/availability time. These public trades are a proxy, not
// evidence that any trade was a liquidation. No network access is performed.
func NormalizeRecentTrades(coin string, rows []RecentTrade) ([]ProxyTrade, error) {
	if coin == "" || strings.TrimSpace(coin) != coin {
		return nil, fmt.Errorf("invalid coin")
	}
	out := make([]ProxyTrade, 0, len(rows))
	for i, row := range rows {
		price, e1 := strconv.ParseFloat(row.Price, 64)
		size, e2 := strconv.ParseFloat(row.Size, 64)
		side := strings.ToUpper(row.Side)
		if e1 != nil || e2 != nil || !sizingFinite(price) || !sizingFinite(size) || price <= 0 || size <= 0 || row.TimeMS <= 0 || row.TimeMS > math.MaxInt64/1_000_000 || row.TradeID < 0 || (side != "B" && side != "A") {
			return nil, fmt.Errorf("invalid trade at index %d", i)
		}
		notional := price * size
		if !sizingFinite(notional) {
			return nil, fmt.Errorf("trade notional overflow at index %d", i)
		}
		if side == "B" {
			side = "BUY"
		} else {
			side = "SELL"
		}
		var hash *string
		if row.Hash != nil {
			value := *row.Hash
			hash = &value
		}
		out = append(out, ProxyTrade{strings.ToUpper(coin) + "USDT", side, price, size, row.TimeMS, row.TradeID, sizingRound(notional, 2), hash, "hyperliquid_public"})
	}
	return out, nil
}

// AggregateTradeProxy groups by event time into half-open UTC 4h buckets.
// Buckets are not point-in-time safe until the interval is complete and all
// relevant trades have been received; receipt times/completeness are unknown.
// No deduplication is performed. Counts and amounts are trade-flow proxies,
// not liquidation truth. Mixed sources are rejected, including across buckets.
func AggregateTradeProxy(trades []ProxyTrade, minimum float64) ([]TradeProxyBucket, error) {
	if !sizingFinite(minimum) || minimum < 0 {
		return nil, fmt.Errorf("invalid minimum")
	}
	type key struct {
		symbol string
		start  int64
	}
	groups := make(map[key]*TradeProxyBucket)
	source := ""
	for i, t := range trades {
		if t.Symbol == "" || strings.TrimSpace(t.Symbol) != t.Symbol || t.Source == "" || strings.TrimSpace(t.Source) != t.Source || (t.Side != "BUY" && t.Side != "SELL") || !sizingFinite(t.Price) || !sizingFinite(t.Size) || !sizingFinite(t.NotionalUSD) || t.Price <= 0 || t.Size <= 0 || t.NotionalUSD < 0 || t.TimeMS <= 0 || t.TimeMS > math.MaxInt64/1_000_000 || t.TradeID < 0 {
			return nil, fmt.Errorf("invalid trade at index %d", i)
		}
		notional := t.Price * t.Size
		if !sizingFinite(notional) || sizingRound(notional, 2) != t.NotionalUSD {
			return nil, fmt.Errorf("inconsistent trade notional at index %d", i)
		}
		if source != "" && source != t.Source {
			return nil, fmt.Errorf("mixed sources")
		}
		source = t.Source
		ts := t.TimeMS * 1_000_000
		start := ts / proxyFourHourNS * proxyFourHourNS
		if start > math.MaxInt64-proxyFourHourNS {
			return nil, fmt.Errorf("bucket end overflow")
		}
		if t.NotionalUSD < minimum {
			continue
		}
		k := key{strings.ToUpper(t.Symbol), start}
		b := groups[k]
		if b == nil {
			b = &TradeProxyBucket{StartNS: start, EndNS: start + proxyFourHourNS, Symbol: k.symbol, Source: t.Source}
			groups[k] = b
		}
		if t.Side == "SELL" {
			b.LongCount++
			b.LongUSD += t.NotionalUSD
		} else {
			b.ShortCount++
			b.ShortUSD += t.NotionalUSD
		}
		if !sizingFinite(b.LongUSD) || !sizingFinite(b.ShortUSD) {
			return nil, fmt.Errorf("bucket sum overflow")
		}
		b.MaxUSD = math.Max(b.MaxUSD, t.NotionalUSD)
	}
	out := make([]TradeProxyBucket, 0, len(groups))
	for _, b := range groups {
		b.LongUSD = sizingRound(b.LongUSD, 2)
		b.ShortUSD = sizingRound(b.ShortUSD, 2)
		total := b.LongUSD + b.ShortUSD
		if !sizingFinite(total) {
			return nil, fmt.Errorf("bucket total overflow")
		}
		b.TotalUSD = sizingRound(total, 2)
		b.MaxUSD = sizingRound(b.MaxUSD, 2)
		if b.TotalUSD > 0 {
			b.Imbalance = sizingRound((b.LongUSD-b.ShortUSD)/b.TotalUSD, 4)
		}
		out = append(out, *b)
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Symbol != out[j].Symbol {
			return out[i].Symbol < out[j].Symbol
		}
		return out[i].StartNS < out[j].StartNS
	})
	return out, nil
}
