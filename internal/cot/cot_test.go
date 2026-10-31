package cot

import (
	"fmt"
	"strings"
	"testing"
)

func TestParseCSVRejectsInvalidBitcoinReports(t *testing.T) {
	header := "Market_and_Exchange_Names,Report_Date_as_YYYY-MM-DD,Asset_Mgr_Positions_Long_All,Asset_Mgr_Positions_Short_All,Lev_Money_Positions_Long_All,Lev_Money_Positions_Short_All,Dealer_Positions_Long_All,Dealer_Positions_Short_All,Open_Interest_All\n"
	cases := map[string]string{
		"missing column":      "Market_and_Exchange_Names,Report_Date_as_YYYY-MM-DD\nBITCOIN,2026-01-06\n",
		"missing date column": strings.Replace(header, "Report_Date_as_YYYY-MM-DD", "other", 1) + "BITCOIN,2026-01-06,1,2,3,4,5,6,7\n",
		"invalid date":        header + "BITCOIN,bad,1,2,3,4,5,6,7\n",
		"short row":           header + "BITCOIN,2026-01-06,1,2\n",
		"invalid number":      header + "BITCOIN,2026-01-06,abc,2,3,4,5,6,7\n",
		"integer overflow":    header + "BITCOIN,2026-01-06,9223372036854775808,0,0,0,0,0,0\n",
		"negative count":      header + "BITCOIN,2026-01-06,9223372036854775807,-1,0,0,0,0,0\n",
	}
	for i := 0; i < 7; i++ {
		counts := []string{"1", "2", "3", "4", "5", "6", "7"}
		counts[i] = "-1"
		cases[fmt.Sprintf("negative field %d", i)] = header + "BITCOIN,2026-01-06," + strings.Join(counts, ",") + "\n"
	}
	for name, input := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := parseCSV([]byte(input), ""); err == nil {
				t.Fatal("expected error")
			}
		})
	}
}
