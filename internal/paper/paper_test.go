package paper

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

const testNow uint64 = 1800000000000

func tempRoot(t *testing.T) string {
	t.Helper()
	p, e := filepath.EvalSymlinks(t.TempDir())
	if e != nil {
		t.Fatal(e)
	}
	return p
}

func fixture(t *testing.T, name string) object {
	t.Helper()
	b, e := os.ReadFile("../../rust/options-workbench/fixtures/manual-paper-" + name + ".json")
	if e != nil {
		t.Fatal(e)
	}
	v, e := parse(b, 1048576, "command")
	if e != nil {
		t.Fatal(e)
	}
	return v
}
func call(t *testing.T, s *Store, route string, v any, now uint64, h hooks) object {
	t.Helper()
	b, e := s.api("/api/paper/"+route, canonical(v), func() uint64 { return now }, h)
	if e != nil {
		t.Fatal(e)
	}
	r, e := decode(b, maxStoreBytes)
	if e != nil {
		t.Fatal(e)
	}
	return obj(r)
}
func setup(t *testing.T) *Store {
	t.Helper()
	s := NewStore(filepath.Join(tempRoot(t), "journal"))
	commitTest(t, s, fixture(t, "settings"))
	return s
}
func commitTest(t *testing.T, s *Store, c object) object {
	t.Helper()
	p := call(t, s, "preview", c, testNow, hooks{})["preview"]
	return call(t, s, "confirm", object{"preview": p, "acknowledged_record_only": true}, testNow, hooks{})
}
func state(t *testing.T, s *Store) object { return call(t, s, "state", object{}, testNow, hooks{}) }
func assertErr(t *testing.T, e error, want string) {
	t.Helper()
	if e == nil || e.Error() != want {
		t.Fatalf("error %v, want %s", e, want)
	}
}
func TestPreviewReplayWithdraw(t *testing.T) {
	s := NewStore(filepath.Join(tempRoot(t), "journal"))
	p := call(t, s, "preview", fixture(t, "settings"), testNow, hooks{})["preview"]
	if _, e := os.Stat(s.path); !os.IsNotExist(e) {
		t.Fatal("preview initialized store")
	}
	call(t, s, "confirm", object{"preview": p, "acknowledged_record_only": true}, testNow, hooks{})
	before, _ := os.ReadFile(filepath.Join(s.path, "snapshot.json"))
	p = call(t, s, "preview", fixture(t, "intent"), testNow, hooks{})["preview"]
	after, _ := os.ReadFile(filepath.Join(s.path, "snapshot.json"))
	if !bytes.Equal(before, after) {
		t.Fatal("preview mutated store")
	}
	c := object{"preview": p, "acknowledged_record_only": true}
	r := call(t, s, "confirm", c, testNow, hooks{})
	v := obj(r["view"])
	record := obj(v["intents"].([]any)[0])
	if record["status"] != "RECORDED_UNASSESSED" || v["unallocated_usd"] != "10000" {
		t.Fatal(v)
	}
	w := object{"schema_version": commandSchema, "request_id": "withdraw", "action": object{"kind": "WITHDRAW_INTENT", "value": object{"intent_id": obj(record["intent"])["intent_id"], "intent_sha256": record["intent_sha256"]}}}
	commitTest(t, s, w)
	before, _ = os.ReadFile(filepath.Join(s.path, "snapshot.json"))
	r = call(t, NewStore(s.path), "confirm", c, testNow+previewTTL+100, hooks{})
	after, _ = os.ReadFile(filepath.Join(s.path, "snapshot.json"))
	if r["replayed"] != true || !bytes.Equal(before, after) || obj(obj(r["view"])["intents"].([]any)[0])["status"] != "WITHDRAWN_RECORD_ONLY" {
		t.Fatal(r)
	}
	again := call(t, s, "preview", fixture(t, "intent"), testNow+previewTTL+100, hooks{})["preview"]
	if !bytes.Equal(canonical(p), canonical(again)) {
		t.Fatal("retry preview changed")
	}
}
func TestAllocationAndStrictContracts(t *testing.T) {
	for _, amount := range []string{"40000.0000000000000000000000000001", "100000"} {
		c := fixture(t, "settings")
		obj(obj(obj(obj(c["action"])["value"])["binance_usdt"])["allocation"])["allocation_usd"] = amount
		_, e := preview(emptyJournal(), c, testNow)
		assertErr(t, e, "OverAllocated")
	}
	for _, field := range []string{"margin_mode", "max_leverage"} {
		c := fixture(t, "settings")
		obj(obj(obj(c["action"])["value"])["binance_usdt"])[field] = nil
		_, e := preview(emptyJournal(), c, testNow)
		assertErr(t, e, "RiskSettingsMissing")
	}
	c := fixture(t, "settings")
	obj(obj(obj(c["action"])["value"])["deribit_usdc"])["conversion"] = nil
	_, e := preview(emptyJournal(), c, testNow)
	assertErr(t, e, "ConversionUnconfirmed")
	for _, b := range []string{`{"request_id":"a","request_id":"a"}`, `{"path":"/etc/passwd"}`, `null`, `{} {}`, `{"x":1.5}`} {
		if _, e := parse([]byte(b), 1000, "empty"); e == nil {
			t.Fatalf("accepted %s", b)
		}
	}
	c = fixture(t, "intent")
	obj(obj(obj(obj(c["action"])["value"])["instrument"])["details"])["expiry_ms"] = testNow
	if _, e := parse(canonical(c), 1048576, "command"); e == nil {
		t.Fatal("accepted wrong product field")
	}
}
func TestFreshnessTamperingAndClock(t *testing.T) {
	s := setup(t)
	p := obj(call(t, s, "preview", fixture(t, "intent"), testNow, hooks{})["preview"])
	c := object{"preview": p, "acknowledged_record_only": true}
	_, e := s.api("/api/paper/confirm", canonical(c), func() uint64 { return testNow + previewTTL }, hooks{})
	assertErr(t, e, "StalePreview")
	_, e = s.api("/api/paper/confirm", canonical(c), func() uint64 { return testNow - 1 }, hooks{})
	assertErr(t, e, "ClockRegression")
	obj(obj(obj(p["command"])["action"])["value"])["requested_budget_usd"] = "200"
	_, e = s.api("/api/paper/confirm", canonical(c), func() uint64 { return testNow }, hooks{})
	assertErr(t, e, "InvalidPreview")
}
func TestFailureBoundaries(t *testing.T) {
	for _, point := range []string{"before_create", "temp_created", "before_file_sync", "file_synced", "renamed", "directory_synced"} {
		t.Run(point, func(t *testing.T) {
			s := setup(t)
			p := call(t, s, "preview", fixture(t, "intent"), testNow, hooks{})["preview"]
			c := object{"preview": p, "acknowledged_record_only": true}
			_, e := s.api("/api/paper/confirm", canonical(c), func() uint64 { return testNow }, hooks{failAt: point})
			if e == nil {
				t.Fatal("injection succeeded")
			}
			committed := point == "renamed" || point == "directory_synced"
			revision := uint64(1)
			if committed {
				revision = 2
			}
			if num(obj(state(t, s)["view"]), "revision") != revision {
				t.Fatal("partial revision")
			}
			r := call(t, s, "confirm", c, testNow, hooks{})
			if r["replayed"] != committed || num(obj(r["view"]), "revision") != 2 {
				t.Fatal(r)
			}
		})
	}
}
func TestCorruptionAndLinks(t *testing.T) {
	for _, mode := range []string{"missing", "corrupt", "symlink", "hardlink", "duplicate-history", "regressed-history", "whitespace"} {
		t.Run(mode, func(t *testing.T) {
			s := setup(t)
			commitTest(t, s, fixture(t, "intent"))
			p := filepath.Join(s.path, "snapshot.json")
			b, _ := os.ReadFile(p)
			switch mode {
			case "missing":
				os.Remove(p)
			case "corrupt":
				os.WriteFile(p, []byte(`{}`), 0600)
			case "symlink":
				saved := filepath.Join(filepath.Dir(s.path), "saved")
				os.Rename(p, saved)
				os.Symlink(saved, p)
			case "hardlink":
				os.Link(p, filepath.Join(filepath.Dir(s.path), "saved"))
			case "whitespace":
				os.WriteFile(p, append(b, '\n'), 0600)
			default:
				env, _ := parse(b, maxStoreBytes, "envelope")
				j := obj(env["journal"])
				entries := j["entries"].([]any)
				if mode == "duplicate-history" {
					j["entries"] = append(entries, entries[0])
				} else {
					obj(entries[1])["confirmed_ms"] = testNow - 1
				}
				env["sha256"] = digest(j)
				os.WriteFile(p, canonical(env), 0600)
			}
			if _, _, e := s.read(); e == nil {
				t.Fatal("accepted corrupted state")
			}
		})
	}
	s := NewStore(filepath.Join(tempRoot(t), "journal"))
	p := call(t, s, "preview", fixture(t, "settings"), testNow, hooks{})["preview"]
	_, e := s.api("/api/paper/confirm", canonical(object{"preview": p, "acknowledged_record_only": true}), func() uint64 { return testNow }, hooks{failAt: "file_synced"})
	if e == nil {
		t.Fatal("expected failed initialization")
	}
	if _, _, e = s.read(); e != nil {
		t.Fatal("failed initialization must remain retryable", e)
	}
	call(t, s, "confirm", object{"preview": p, "acknowledged_record_only": true}, testNow, hooks{})
}
func TestConcurrentConfirmations(t *testing.T) {
	s := setup(t)
	cs := make([][]byte, 2)
	for i := range cs {
		c := fixture(t, "settings")
		c["request_id"] = []string{"a", "b"}[i]
		p := call(t, s, "preview", c, testNow, hooks{})["preview"]
		cs[i] = canonical(object{"preview": p, "acknowledged_record_only": true})
	}
	start := make(chan struct{})
	out := make(chan error, 2)
	var wg sync.WaitGroup
	for _, c := range cs {
		wg.Add(1)
		go func(b []byte) {
			defer wg.Done()
			<-start
			_, e := NewStore(s.path).api("/api/paper/confirm", b, func() uint64 { return testNow }, hooks{})
			out <- e
		}(c)
	}
	close(start)
	wg.Wait()
	close(out)
	success := 0
	for e := range out {
		if e == nil {
			success++
		}
	}
	if success != 1 || num(obj(state(t, s)["view"]), "revision") != 2 {
		t.Fatal("concurrent mutation lost serialization")
	}
}
func TestClockUnderLock(t *testing.T) {
	s := setup(t)
	p := call(t, s, "preview", fixture(t, "intent"), testNow, hooks{})["preview"]
	_, e := s.api("/api/paper/confirm", canonical(object{"preview": p, "acknowledged_record_only": true}), func() uint64 {
		l, e := lock(s.path)
		if e == nil {
			l.close()
			t.Fatal("clock sampled without lock")
		}
		return testNow + previewTTL
	}, hooks{})
	assertErr(t, e, "StalePreview")
}
func TestDecimalAndCanonical(t *testing.T) {
	for input, want := range map[string]string{"-0.000": "0", ".0010": "0.001", "000100.1000": "100.1"} {
		_, got, e := decimal(input)
		if e != nil || got != want {
			t.Fatalf("%s = %s %v", input, got, e)
		}
	}
	for _, s := range []string{"1e1", "+1", "1.", "1/2", strings.Repeat("9", 65), "0." + strings.Repeat("0", 28) + "1"} {
		if _, _, e := decimal(s); e == nil {
			t.Fatal(s)
		}
	}
	got := string(canonical(object{"z": "<>& ", "a": json.Number("18446744073709551615")}))
	if got != "{\"a\":18446744073709551615,\"z\":\"<>& \"}" {
		t.Fatal(got)
	}
}
func TestStrictUnicodeAndRequiredFields(t *testing.T) {
	for _, b := range []string{`"\ud800"`, `"\udc00"`, `"\ud800A"`} {
		if _, e := decode([]byte(b), 1000); e == nil {
			t.Fatal("accepted unpaired surrogate", b)
		}
	}
	if _, e := decode([]byte(`"😀"`), 1000); e != nil {
		t.Fatal(e)
	}
	c := fixture(t, "intent")
	delete(obj(obj(obj(obj(c["action"])["value"])["instrument"])["details"]), "reduce_only")
	if _, e := parse(canonical(c), 1048576, "command"); e == nil {
		t.Fatal("missing required bool accepted")
	}
}

func TestStorageScope(t *testing.T) {
	path := filepath.Join(tempRoot(t), "journal")
	for store, want := range map[*Store]string{NewStore(path): "REPOSITORY_SCOPED_SIMULATION_DECLARATION", NewEngineeringStore(path): "ENGINEERING_TEST_ONLY"} {
		if got := state(t, store)["storage_scope"]; got != want {
			t.Fatal(got, want)
		}
	}
}

func TestIntentBoundaries(t *testing.T) {
	s := setup(t)
	j, _, e := s.read()
	if e != nil {
		t.Fatal(e)
	}
	for key, value := range map[string]any{"symbol": "BTCUSD_PERP", "quantity_btc": "0", "leverage": "3", "margin_mode": "CROSS"} {
		c := fixture(t, "intent")
		obj(obj(obj(obj(c["action"])["value"])["instrument"])["details"])[key] = value
		if _, e := preview(j, c, testNow); e == nil {
			t.Fatal(key)
		}
	}
	c := fixture(t, "intent")
	i := obj(obj(c["action"])["value"])
	i["requested_budget_usd"] = "1000.00000001"
	_, e = preview(j, c, testNow)
	assertErr(t, e, "SingleIntentLimit")
	c = fixture(t, "intent")
	i = obj(obj(c["action"])["value"])
	ref := object{"sha256": strings.Repeat("a", 64), "source": "engineering-only", "kind": "SYNTHETIC_ENGINEERING_ONLY", "observed_ms": testNow - 2, "available_ms": testNow - 1, "valid_until_ms": testNow + 2}
	obj(i["evidence"])["fees"] = ref
	if _, e = preview(j, c, testNow); e != nil {
		t.Fatal(e)
	}
	if !strings.Contains(string(canonical(blockers(i, testNow))), "FEES_UNVERIFIED") || !strings.Contains(string(canonical(blockers(i, testNow+2))), "FEES_STALE") {
		t.Fatal("evidence permission changed")
	}
	ref["available_ms"] = testNow + 1
	if _, e = preview(j, c, testNow); e == nil {
		t.Fatal("future evidence accepted")
	}
	c = fixture(t, "intent")
	i = obj(obj(c["action"])["value"])
	i["instrument"] = object{"product": "DERIBIT_LINEAR_USDC_OPTION_BUY", "details": object{"schema_version": "manual-deribit-option.rust.v1", "symbol": "BTC_USDC-29JAN27-60000-C", "underlying": "BTC", "expiry_ms": testNow + 1000000, "option_type": "C", "strike_usdc": "60000", "quantity_contracts": "1", "limit_usdc_per_base": "1500"}}
	if _, e = preview(j, c, testNow); e != nil {
		t.Fatal(e)
	}
	obj(obj(i["instrument"])["details"])["option_type"] = "P"
	if _, e = preview(j, c, testNow); e == nil {
		t.Fatal("option identity mismatch accepted")
	}
}

func TestRustJournalParity(t *testing.T) {
	b, e := os.ReadFile("testdata/rust-journal.json")
	if e != nil {
		t.Fatal(e)
	}
	b = bytes.TrimSuffix(b, []byte("\n"))
	env, e := parse(b, maxStoreBytes, "envelope")
	if e != nil {
		t.Fatal(e)
	}
	j := obj(env["journal"])
	if e = validateJournal(j); e != nil {
		t.Fatal(e)
	}
	if digest(j) != env["sha256"] {
		t.Fatal("Rust hash mismatch")
	}
	s := setup(t)
	commitTest(t, s, fixture(t, "intent"))
	got, e := os.ReadFile(filepath.Join(s.path, "snapshot.json"))
	if e != nil {
		t.Fatal(e)
	}
	if !bytes.Equal(got, b) {
		t.Fatalf("Go journal differs from Rust\n%s\n%s", got, b)
	}
	os.WriteFile(filepath.Join(s.path, "snapshot.json"), b, 0600)
	if num(obj(state(t, s)["view"]), "revision") != 2 {
		t.Fatal("Rust snapshot did not reopen")
	}
}

func TestCrashWorker(t *testing.T) {
	path := os.Getenv("ZQ_GO_PAPER_CRASH_STORE")
	if path == "" {
		return
	}
	b, e := os.ReadFile(os.Getenv("ZQ_GO_PAPER_CRASH_INPUT"))
	if e != nil {
		t.Fatal(e)
	}
	result, e := NewStore(path).api("/api/paper/confirm", b, func() uint64 { return testNow }, hooks{observe: func(point string) {
		if point == os.Getenv("ZQ_GO_PAPER_CRASH_PHASE") {
			os.WriteFile(os.Getenv("ZQ_GO_PAPER_CRASH_MARKER"), []byte(point), 0600)
			if release := os.Getenv("ZQ_GO_PAPER_RELEASE"); release != "" {
				for {
					if _, err := os.Stat(release); err == nil {
						break
					}
					time.Sleep(5 * time.Millisecond)
				}
			} else {
				// Keep a timer pending so a non-race child does not exit with
				// the runtime's all-goroutines-asleep deadlock diagnostic.
				for {
					time.Sleep(time.Second)
				}
			}
		}
	}})
	if output := os.Getenv("ZQ_GO_PAPER_RESULT"); output != "" {
		if e != nil {
			result = []byte(e.Error())
		}
		if err := os.WriteFile(output, result, 0600); err != nil {
			t.Fatal(err)
		}
		return
	}
	if e != nil {
		t.Fatal(e)
	}
}
func TestStageRecoveryPreservesUnknown(t *testing.T) {
	root := tempRoot(t)
	path := filepath.Join(root, "journal")
	external := filepath.Join(root, "external")
	if err := os.WriteFile(external, []byte("external"), 0600); err != nil {
		t.Fatal(err)
	}
	for _, kind := range []string{"unknown", "symlink", "hardlink", "unmarked", "other-target", "journal-symlink"} {
		t.Run(kind, func(t *testing.T) {
			stage := filepath.Join(root, ".paper-init-v1-"+kind)
			if err := os.Mkdir(stage, 0700); err != nil {
				t.Fatal(err)
			}
			if kind != "unmarked" {
				marker := stageMarker(path)
				if kind == "other-target" {
					marker = stageMarker(filepath.Join(root, "another"))
				}
				if err := os.WriteFile(filepath.Join(stage, "owner"), marker, 0600); err != nil {
					t.Fatal(err)
				}
			}
			journal := filepath.Join(stage, "journal")
			if kind == "journal-symlink" {
				if err := os.Symlink(root, journal); err != nil {
					t.Fatal(err)
				}
			} else {
				if err := os.Mkdir(journal, 0700); err != nil {
					t.Fatal(err)
				}
				file := filepath.Join(journal, "snapshot.json")
				var err error
				switch kind {
				case "symlink":
					err = os.Symlink(external, file)
				case "hardlink":
					err = os.Link(external, file)
				default:
					err = os.WriteFile(file, []byte("data"), 0600)
				}
				if err != nil {
					t.Fatal(err)
				}
				if kind == "unknown" {
					if err = os.WriteFile(filepath.Join(stage, "unexpected"), []byte("keep"), 0600); err != nil {
						t.Fatal(err)
					}
				}
			}
			for i := 0; i < 20; i++ {
				if err := recoverStages(path, hooks{}); err != nil {
					t.Fatal(err)
				}
			}
			if _, err := os.Lstat(journal); err != nil {
				t.Fatal(err)
			}
			if kind == "unknown" {
				if b, err := os.ReadFile(filepath.Join(journal, "snapshot.json")); err != nil || string(b) != "data" {
					t.Fatal("snapshot changed", err)
				}
				if b, err := os.ReadFile(filepath.Join(stage, "unexpected")); err != nil || string(b) != "keep" {
					t.Fatal("unknown changed", err)
				}
			}
			if b, err := os.ReadFile(external); err != nil || string(b) != "external" {
				t.Fatal("external changed", err)
			}
		})
	}
}

func TestStageRecoveryInterruptedCleanup(t *testing.T) {
	for _, phase := range []string{"cleanup_files", "cleanup_journal"} {
		t.Run(phase, func(t *testing.T) {
			root := tempRoot(t)
			path := filepath.Join(root, "journal")
			stage := filepath.Join(root, ".paper-init-v1-interrupted")
			if err := os.MkdirAll(filepath.Join(stage, "journal"), 0700); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(stage, "owner"), stageMarker(path), 0600); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(stage, "journal", "snapshot.json"), []byte("data"), 0600); err != nil {
				t.Fatal(err)
			}
			if err := recoverStages(path, hooks{failAt: phase}); err == nil {
				t.Fatal("missing injected failure")
			}
			if _, err := os.Stat(filepath.Join(stage, "owner")); err != nil {
				t.Fatal("marker removed early", err)
			}
			if err := recoverStages(path, hooks{}); err != nil {
				t.Fatal(err)
			}
			if _, err := os.Lstat(stage); !os.IsNotExist(err) {
				t.Fatal("stage remains", err)
			}
		})
	}
}

func TestCrossLanguageFirstConfirmation(t *testing.T) {
	rust := os.Getenv("ZQ_RUST_PAPER_TEST_BINARY")
	if rust == "" {
		t.Skip("Linux cross-language helper not configured")
	}
	for _, phase := range []string{"before_publish", "published"} {
		t.Run(phase, func(t *testing.T) {
			s := NewStore(filepath.Join(tempRoot(t), "journal"))
			p := call(t, s, "preview", fixture(t, "settings"), testNow, hooks{})["preview"]
			c := object{"preview": p, "acknowledged_record_only": true}
			root := filepath.Dir(s.path)
			input, marker := filepath.Join(root, "input"), filepath.Join(root, "marker")
			if err := os.WriteFile(input, canonical(c), 0600); err != nil {
				t.Fatal(err)
			}
			cmd := exec.Command(rust, "--exact", "paper_store::tests::process_worker", "--nocapture")
			output, release := filepath.Join(root, "rust-result"), filepath.Join(root, "release")
			cmd.Env = append(os.Environ(), "ZQ_PAPER_CRASH_STORE="+s.path, "ZQ_PAPER_CRASH_INPUT="+input, "ZQ_PAPER_CRASH_MARKER="+marker, "ZQ_PAPER_CRASH_PHASE="+phase, "ZQ_PAPER_RESULT="+output, "ZQ_PAPER_RELEASE="+release)
			if err := cmd.Start(); err != nil {
				t.Fatal(err)
			}
			deadline := time.Now().Add(10 * time.Second)
			for time.Now().Before(deadline) {
				if _, err := os.Stat(marker); err == nil {
					break
				}
				time.Sleep(10 * time.Millisecond)
			}
			if _, err := os.Stat(marker); err != nil {
				cmd.Process.Kill()
				cmd.Wait()
				t.Fatal(err)
			}
			if phase == "before_publish" {
				r := call(t, s, "confirm", c, testNow, hooks{})
				if r["replayed"] != false {
					t.Fatal(r)
				}
			} else {
				_, err := s.api("/api/paper/confirm", canonical(c), func() uint64 { return testNow }, hooks{})
				if err == nil {
					t.Fatal("Rust lock did not exclude Go")
				}
			}
			if err := os.WriteFile(release, nil, 0600); err != nil {
				t.Fatal(err)
			}
			if err := cmd.Wait(); err != nil {
				t.Fatal(err)
			}
			b, err := os.ReadFile(output)
			if err != nil {
				t.Fatal(err)
			}
			v, err := decode(b, maxStoreBytes)
			if err != nil || obj(v)["replayed"] != (phase == "before_publish") {
				t.Fatalf("Rust result: %s %v", b, err)
			}
			r := call(t, s, "confirm", c, testNow, hooks{})
			if r["replayed"] != true {
				t.Fatal(r)
			}
		})
	}
}

func TestCrossLanguageStageRecovery(t *testing.T) {
	rust := os.Getenv("ZQ_RUST_PAPER_TEST_BINARY")
	if rust == "" {
		t.Skip("Linux cross-language helper not configured")
	}
	for _, writer := range []string{"go", "rust"} {
		for _, phase := range []string{"temp_created", "before_publish", "published"} {
			t.Run(writer+"/"+phase, func(t *testing.T) {
				root := tempRoot(t)
				s := NewStore(filepath.Join(root, "journal"))
				p := call(t, s, "preview", fixture(t, "settings"), testNow, hooks{})["preview"]
				c := object{"preview": p, "acknowledged_record_only": true}
				input, ready, output := filepath.Join(root, "input"), filepath.Join(root, "ready"), filepath.Join(root, "output")
				if err := os.WriteFile(input, canonical(c), 0600); err != nil {
					t.Fatal(err)
				}
				var cmd *exec.Cmd
				if writer == "go" {
					exe, err := os.Executable()
					if err != nil {
						t.Fatal(err)
					}
					cmd = exec.Command(exe, "-test.run=^TestCrashWorker$")
					cmd.Env = append(os.Environ(), "ZQ_GO_PAPER_CRASH_STORE="+s.path, "ZQ_GO_PAPER_CRASH_INPUT="+input, "ZQ_GO_PAPER_CRASH_MARKER="+ready, "ZQ_GO_PAPER_CRASH_PHASE="+phase)
				} else {
					cmd = exec.Command(rust, "--exact", "paper_store::tests::process_worker")
					cmd.Env = append(os.Environ(), "ZQ_PAPER_CRASH_STORE="+s.path, "ZQ_PAPER_CRASH_INPUT="+input, "ZQ_PAPER_CRASH_MARKER="+ready, "ZQ_PAPER_CRASH_PHASE="+phase)
				}
				if err := cmd.Start(); err != nil {
					t.Fatal(err)
				}
				defer func() {
					if cmd.ProcessState == nil {
						cmd.Process.Kill()
						cmd.Wait()
					}
				}()
				deadline := time.Now().Add(10 * time.Second)
				for {
					if _, err := os.Stat(ready); err == nil {
						break
					}
					if time.Now().After(deadline) {
						t.Fatal("worker checkpoint timeout")
					}
					time.Sleep(5 * time.Millisecond)
				}
				stages, _ := filepath.Glob(filepath.Join(root, ".paper-init-v1-*"))
				if len(stages) != 1 {
					t.Fatalf("expected active stage: %v", stages)
				}
				if writer == "go" {
					helper := exec.Command(rust, "--exact", "paper_store::tests::process_worker")
					helper.Env = append(os.Environ(), "ZQ_PAPER_CRASH_STORE="+s.path, "ZQ_PAPER_RECOVER_ONLY=1")
					if b, err := helper.CombinedOutput(); err != nil {
						t.Fatalf("Rust active recovery: %s %v", b, err)
					}
				} else if err := recoverStages(s.path, hooks{}); err != nil {
					t.Fatal(err)
				}
				if _, err := os.Stat(stages[0]); err != nil {
					t.Fatal("active stage removed", err)
				}
				if err := cmd.Process.Kill(); err != nil {
					t.Fatal(err)
				}
				cmd.Wait()
				var snapshot []byte
				if phase == "published" {
					var err error
					snapshot, err = os.ReadFile(filepath.Join(s.path, "snapshot.json"))
					if err != nil {
						t.Fatal(err)
					}
				}
				if writer == "rust" {
					r := call(t, s, "confirm", c, testNow, hooks{})
					if r["replayed"] != (phase == "published") {
						t.Fatal(r)
					}
				} else {
					helper := exec.Command(rust, "--exact", "paper_store::tests::process_worker")
					helper.Env = append(os.Environ(), "ZQ_PAPER_CRASH_STORE="+s.path, "ZQ_PAPER_CRASH_INPUT="+input, "ZQ_PAPER_RESULT="+output)
					if b, err := helper.CombinedOutput(); err != nil {
						t.Fatalf("Rust retry: %s %v", b, err)
					}
				}
				if _, err := os.Lstat(stages[0]); !os.IsNotExist(err) {
					t.Fatal("abandoned stage remains", err)
				}
				if snapshot != nil {
					b, err := os.ReadFile(filepath.Join(s.path, "snapshot.json"))
					if err != nil || !bytes.Equal(snapshot, b) {
						t.Fatal("published snapshot changed", err)
					}
				}
			})
		}
	}
}

func TestCrossLanguageGoLockExcludesRust(t *testing.T) {
	rust := os.Getenv("ZQ_RUST_PAPER_TEST_BINARY")
	if rust == "" {
		t.Skip("Linux cross-language helper not configured")
	}
	root := tempRoot(t)
	s := NewStore(filepath.Join(root, "journal"))
	p := call(t, s, "preview", fixture(t, "settings"), testNow, hooks{})["preview"]
	input, marker, release, output := filepath.Join(root, "input"), filepath.Join(root, "marker"), filepath.Join(root, "release"), filepath.Join(root, "output")
	if err := os.WriteFile(input, canonical(object{"preview": p, "acknowledged_record_only": true}), 0600); err != nil {
		t.Fatal(err)
	}
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(exe, "-test.run=^TestCrashWorker$")
	cmd.Env = append(os.Environ(), "ZQ_GO_PAPER_CRASH_STORE="+s.path, "ZQ_GO_PAPER_CRASH_INPUT="+input, "ZQ_GO_PAPER_CRASH_MARKER="+marker, "ZQ_GO_PAPER_CRASH_PHASE=published", "ZQ_GO_PAPER_RELEASE="+release, "ZQ_GO_PAPER_RESULT="+output)
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() {
		if cmd.ProcessState == nil {
			cmd.Process.Kill()
			cmd.Wait()
		}
	}()
	deadline := time.Now().Add(10 * time.Second)
	for {
		if _, err := os.Stat(marker); err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("missing Go checkpoint")
		}
		time.Sleep(5 * time.Millisecond)
	}
	runRust := func() ([]byte, error) {
		helper := exec.Command(rust, "--exact", "paper_store::tests::process_worker")
		helper.Env = append(os.Environ(), "ZQ_PAPER_CRASH_STORE="+s.path, "ZQ_PAPER_CRASH_INPUT="+input, "ZQ_PAPER_RESULT="+filepath.Join(root, "rust-output"))
		return helper.CombinedOutput()
	}
	if b, err := runRust(); err == nil || !bytes.Contains(b, []byte("Busy")) {
		t.Fatalf("Rust must be excluded: %s %v", b, err)
	}
	if err := os.WriteFile(release, nil, 0600); err != nil {
		t.Fatal(err)
	}
	if err := cmd.Wait(); err != nil {
		t.Fatal(err)
	}
	if b, err := runRust(); err != nil {
		t.Fatalf("Rust replay: %s %v", b, err)
	}
	b, err := os.ReadFile(filepath.Join(root, "rust-output"))
	if err != nil {
		t.Fatal(err)
	}
	v, err := decode(b, maxStoreBytes)
	if err != nil || obj(v)["replayed"] != true {
		t.Fatalf("Rust replay: %s %v", b, err)
	}
}

func TestProcessConcurrentFirstConfirmations(t *testing.T) {
	for _, distinct := range []bool{false, true} {
		t.Run(fmt.Sprint(distinct), func(t *testing.T) {
			root := tempRoot(t)
			s := NewStore(filepath.Join(root, "journal"))
			p := call(t, s, "preview", fixture(t, "settings"), testNow, hooks{})["preview"]
			c := object{"preview": p, "acknowledged_record_only": true}
			other := c
			if distinct {
				command := fixture(t, "settings")
				command["request_id"] = "other"
				other = object{"preview": call(t, s, "preview", command, testNow, hooks{})["preview"], "acknowledged_record_only": true}
			}
			exe, err := os.Executable()
			if err != nil {
				t.Fatal(err)
			}
			release := filepath.Join(root, "release")
			var cmds []*exec.Cmd
			for i, item := range []object{c, other} {
				input := filepath.Join(root, fmt.Sprintf("input%d", i))
				marker := filepath.Join(root, fmt.Sprintf("marker%d", i))
				output := filepath.Join(root, fmt.Sprintf("output%d", i))
				if err := os.WriteFile(input, canonical(item), 0600); err != nil {
					t.Fatal(err)
				}
				cmd := exec.Command(exe, "-test.run=^TestCrashWorker$")
				cmd.Env = append(os.Environ(), "ZQ_GO_PAPER_CRASH_STORE="+s.path, "ZQ_GO_PAPER_CRASH_INPUT="+input, "ZQ_GO_PAPER_CRASH_MARKER="+marker, "ZQ_GO_PAPER_CRASH_PHASE=before_publish", "ZQ_GO_PAPER_RELEASE="+release, "ZQ_GO_PAPER_RESULT="+output)
				if err := cmd.Start(); err != nil {
					t.Fatal(err)
				}
				cmds = append(cmds, cmd)
			}
			defer func() {
				for _, cmd := range cmds {
					if cmd.ProcessState == nil {
						cmd.Process.Kill()
						cmd.Wait()
					}
				}
			}()
			deadline := time.Now().Add(10 * time.Second)
			for i := range cmds {
				marker := filepath.Join(root, fmt.Sprintf("marker%d", i))
				for {
					if _, err := os.Stat(marker); err == nil {
						break
					}
					if time.Now().After(deadline) {
						t.Fatal("missing process checkpoint", i)
					}
					time.Sleep(5 * time.Millisecond)
				}
			}
			if err := os.WriteFile(release, nil, 0600); err != nil {
				t.Fatal(err)
			}
			for _, cmd := range cmds {
				if err := cmd.Wait(); err != nil {
					t.Fatal(err)
				}
			}
			committed, replayed, stale := 0, 0, 0
			for i := range cmds {
				b, err := os.ReadFile(filepath.Join(root, fmt.Sprintf("output%d", i)))
				if err != nil {
					t.Fatal(err)
				}
				v, err := decode(b, maxStoreBytes)
				if err != nil {
					if strings.Contains(string(b), "StalePreview") {
						stale++
						continue
					}
					t.Fatalf("unexpected process error %s", b)
				}
				if obj(v)["replayed"] == true {
					replayed++
				} else {
					committed++
				}
			}
			if committed != 1 || (!distinct && (replayed != 1 || stale != 0)) || (distinct && (stale != 1 || replayed != 0)) {
				t.Fatalf("commit=%d replay=%d stale=%d", committed, replayed, stale)
			}
			if num(obj(state(t, s)["view"]), "revision") != 1 {
				t.Fatal("duplicate revision")
			}
		})
	}
}

func TestUnsafeExistingPermissions(t *testing.T) {
	for _, target := range []string{"parent", "journal", "snapshot"} {
		t.Run(target, func(t *testing.T) {
			s := setup(t)
			path := filepath.Dir(s.path)
			if target == "journal" {
				path = s.path
			}
			if target == "snapshot" {
				path = filepath.Join(s.path, "snapshot.json")
			}
			if err := os.Chmod(path, 0777); err != nil {
				t.Fatal(err)
			}
			defer os.Chmod(path, 0700)
			if _, _, err := s.read(); err == nil {
				t.Fatal("accepted unsafe permissions")
			}
		})
	}
}

func TestConcurrentFirstConfirmations(t *testing.T) {
	for _, distinct := range []bool{false, true} {
		t.Run(fmt.Sprint(distinct), func(t *testing.T) {
			s := NewStore(filepath.Join(tempRoot(t), "journal"))
			p := call(t, s, "preview", fixture(t, "settings"), testNow, hooks{})["preview"]
			c := object{"preview": p, "acknowledged_record_only": true}
			other := c
			if distinct {
				command := fixture(t, "settings")
				command["request_id"] = "other"
				other = object{"preview": call(t, s, "preview", command, testNow, hooks{})["preview"], "acknowledged_record_only": true}
			}
			ready := make(chan struct{}, 2)
			release := make(chan struct{})
			result := make(chan error, 2)
			for _, item := range []object{c, other} {
				go func(item object) {
					_, err := s.api("/api/paper/confirm", canonical(item), func() uint64 { return testNow }, hooks{observe: func(phase string) {
						if phase == "before_publish" {
							ready <- struct{}{}
							<-release
						}
					}})
					result <- err
				}(item)
			}
			<-ready
			<-ready
			close(release)
			a, b := <-result, <-result
			if distinct {
				if (a == nil) == (b == nil) {
					t.Fatalf("expected one stale confirmation: %v %v", a, b)
				}
			} else if a != nil || b != nil {
				t.Fatalf("expected commit and replay: %v %v", a, b)
			}
			if num(obj(state(t, s)["view"]), "revision") != 1 {
				t.Fatal("duplicate entry")
			}
		})
	}
}

func TestFirstInitializationBoundaries(t *testing.T) {
	for _, phase := range []string{"before_create", "temp_created", "before_file_sync", "file_synced", "renamed", "directory_synced", "before_publish", "published", "parent_synced"} {
		t.Run(phase, func(t *testing.T) {
			s := NewStore(filepath.Join(tempRoot(t), "journal"))
			p := call(t, s, "preview", fixture(t, "settings"), testNow, hooks{})["preview"]
			c := object{"preview": p, "acknowledged_record_only": true}
			_, err := s.api("/api/paper/confirm", canonical(c), func() uint64 { return testNow }, hooks{failAt: phase})
			if err == nil {
				t.Fatal("expected injected failure")
			}
			committed := phase == "published" || phase == "parent_synced"
			r := call(t, s, "confirm", c, testNow, hooks{})
			if r["replayed"] != committed || num(obj(r["view"]), "revision") != 1 {
				t.Fatal(r)
			}
		})
	}
}

func TestFirstInitializationProcessCrash(t *testing.T) {
	for _, phase := range []string{"temp_created", "file_synced", "renamed", "directory_synced", "before_publish", "published", "parent_synced"} {
		t.Run(phase, func(t *testing.T) {
			s := NewStore(filepath.Join(tempRoot(t), "journal"))
			p := call(t, s, "preview", fixture(t, "settings"), testNow, hooks{})["preview"]
			c := object{"preview": p, "acknowledged_record_only": true}
			root := filepath.Dir(s.path)
			input, marker := filepath.Join(root, "input"), filepath.Join(root, "marker")
			if err := os.WriteFile(input, canonical(c), 0600); err != nil {
				t.Fatal(err)
			}
			exe, err := os.Executable()
			if err != nil {
				t.Fatal(err)
			}
			cmd := exec.Command(exe, "-test.run=^TestCrashWorker$")
			cmd.Env = append(os.Environ(), "ZQ_GO_PAPER_CRASH_STORE="+s.path, "ZQ_GO_PAPER_CRASH_INPUT="+input, "ZQ_GO_PAPER_CRASH_MARKER="+marker, "ZQ_GO_PAPER_CRASH_PHASE="+phase)
			if err = cmd.Start(); err != nil {
				t.Fatal(err)
			}
			deadline := time.Now().Add(10 * time.Second)
			for time.Now().Before(deadline) {
				if _, err = os.Stat(marker); err == nil {
					break
				}
				time.Sleep(10 * time.Millisecond)
			}
			cmd.Process.Kill()
			cmd.Wait()
			if _, err = os.Stat(marker); err != nil {
				t.Fatal("missing checkpoint", err)
			}
			r := call(t, s, "confirm", c, testNow, hooks{})
			if r["replayed"] != (phase == "published" || phase == "parent_synced") {
				t.Fatal(r)
			}
			stages, err := filepath.Glob(filepath.Join(root, ".paper-init-v1-*"))
			if err != nil || len(stages) != 0 {
				t.Fatalf("stages after retry: %v %v", stages, err)
			}
		})
	}
}

func TestProcessCrashRecovery(t *testing.T) {
	for _, phase := range []string{"temp_created", "file_synced", "renamed", "directory_synced"} {
		t.Run(phase, func(t *testing.T) {
			s := setup(t)
			p := call(t, s, "preview", fixture(t, "intent"), testNow, hooks{})["preview"]
			c := object{"preview": p, "acknowledged_record_only": true}
			root := filepath.Dir(s.path)
			input, marker := filepath.Join(root, "input"), filepath.Join(root, "marker")
			os.WriteFile(input, canonical(c), 0600)
			exe, e := os.Executable()
			if e != nil {
				t.Fatal(e)
			}
			cmd := exec.Command(exe, "-test.run=^TestCrashWorker$")
			cmd.Env = append(os.Environ(), "ZQ_GO_PAPER_CRASH_STORE="+s.path, "ZQ_GO_PAPER_CRASH_INPUT="+input, "ZQ_GO_PAPER_CRASH_MARKER="+marker, "ZQ_GO_PAPER_CRASH_PHASE="+phase)
			if e = cmd.Start(); e != nil {
				t.Fatal(e)
			}
			defer cmd.Process.Kill()
			deadline := time.Now().Add(10 * time.Second)
			ready := false
			for time.Now().Before(deadline) {
				if _, e = os.Stat(marker); e == nil {
					ready = true
					break
				}
				time.Sleep(10 * time.Millisecond)
			}
			cmd.Process.Kill()
			cmd.Wait()
			if !ready {
				t.Fatal("crash checkpoint not reached")
			}
			r := call(t, s, "confirm", c, testNow, hooks{})
			if r["replayed"] != (phase == "renamed" || phase == "directory_synced") || num(obj(r["view"]), "revision") != 2 {
				t.Fatal(r)
			}
		})
	}
}
