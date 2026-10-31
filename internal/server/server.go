// Package server serves the local workbench with bounded connections and same-origin writes.
package server

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

const BodyLimit = 1024 * 1024
const deadline = 3 * time.Second

type Evaluate func(string, []byte) ([]byte, error)
type Asset struct {
	MIME string
	Body []byte
}
type Server struct {
	Assets                   map[string]Asset
	Options, Paper, Research Evaluate
}

// LoadAssets admits only a bounded, explicitly built static bundle; never follows symlinks.
func LoadAssets(root string) (map[string]Asset, error) {
	assets := make(map[string]Asset)
	total := int64(0)
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.Type()&os.ModeSymlink != 0 {
			return errors.New("symlink in static bundle")
		}
		if d.IsDir() {
			return nil
		}
		mime := map[string]string{".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}[filepath.Ext(path)]
		if mime == "" {
			return nil
		}
		info, err := d.Info()
		if err != nil {
			return err
		}
		if !info.Mode().IsRegular() {
			return errors.New("non-regular static asset")
		}
		total += info.Size()
		if total > 16*1024*1024 || len(assets) >= 128 {
			return errors.New("static bundle exceeds limit")
		}
		f, err := os.Open(path)
		if err != nil {
			return err
		}
		body, err := io.ReadAll(io.LimitReader(f, 16*1024*1024+1))
		closeErr := f.Close()
		if err != nil {
			return err
		}
		if closeErr != nil {
			return closeErr
		}
		if int64(len(body)) != info.Size() {
			return errors.New("static asset changed while loading")
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		assets["/"+filepath.ToSlash(rel)] = Asset{mime, body}
		return nil
	})
	if err != nil {
		return nil, err
	}
	index, ok := assets["/index.html"]
	if !ok {
		return nil, errors.New("missing index.html")
	}
	assets["/"] = index
	assets["/paper"] = index
	return assets, nil
}

// Serve runs two workers, with no detached per-request goroutines or unbounded queue.
// Closing the listener ends Serve after in-flight connections finish their deadlines.
func (s *Server) Serve(listener net.Listener) error {
	addr, ok := listener.Addr().(*net.TCPAddr)
	if !ok || !addr.IP.IsLoopback() {
		return errors.New("loopback listener required")
	}
	var workers sync.WaitGroup
	errs := make(chan error, 2)
	for i := 0; i < 2; i++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for {
				conn, err := listener.Accept()
				if err != nil {
					errs <- err
					return
				}
				s.handle(conn, listener.Addr().String())
			}
		}()
	}
	workers.Wait()
	close(errs)
	for err := range errs {
		if !errors.Is(err, net.ErrClosed) {
			return err
		}
	}
	return nil
}

func (s *Server) handle(conn net.Conn, host string) {
	defer conn.Close()
	if conn.SetDeadline(time.Now().Add(deadline)) != nil {
		return
	}
	reader := bufio.NewReaderSize(conn, 8192)
	// Validate the raw header first: net/http intentionally accepts identical duplicate lengths.
	var header bytes.Buffer
	for {
		line, err := reader.ReadSlice('\n')
		if err != nil || header.Len()+len(line) > 8192 {
			respond(conn, 400, "application/json", []byte(`{"error":"INVALID_HTTP_REQUEST"}`))
			return
		}
		header.Write(line)
		if bytes.Equal(line, []byte("\r\n")) {
			break
		}
	}
	req, err := readRequest(header.Bytes(), reader, host)
	if err != nil {
		respond(conn, 400, "application/json", []byte(`{"error":"INVALID_HTTP_REQUEST"}`))
		return
	}
	defer req.Body.Close()
	if req.Method == "GET" {
		if a, ok := s.Assets[req.RequestURI]; ok {
			respond(conn, 200, a.MIME, a.Body)
		} else {
			respond(conn, 404, "application/json", []byte(`{"error":"NOT_FOUND"}`))
		}
		return
	}
	body, err := io.ReadAll(io.LimitReader(req.Body, BodyLimit+1))
	if err != nil || int64(len(body)) != req.ContentLength {
		respond(conn, 400, "application/json", []byte(`{"error":"INVALID_HTTP_REQUEST"}`))
		return
	}
	var evaluate Evaluate
	switch req.RequestURI {
	case "/api/curve", "/api/synthetic":
		evaluate = s.Options
	case "/api/paper/state", "/api/paper/preview", "/api/paper/confirm":
		evaluate = s.Paper
	case "/api/research/import", "/api/research/list", "/api/research/snapshot", "/api/research/result", "/api/research/run", "/api/research/compare":
		evaluate = s.Research
	}
	if evaluate == nil {
		respond(conn, 404, "application/json", []byte(`{"error":"NOT_FOUND"}`))
		return
	}
	result, err := evaluate(req.RequestURI, body)
	if err != nil {
		b, _ := json.Marshal(map[string]string{"error": err.Error()})
		respond(conn, 422, "application/json", b)
		return
	}
	respond(conn, 200, "application/json", result)
}

func readRequest(header []byte, body io.Reader, host string) (*http.Request, error) {
	invalid := errors.New("invalid HTTP request")
	lines := strings.Split(string(header), "\r\n")
	seen := make(map[string]bool)
	for _, line := range lines[1:] {
		if line == "" {
			continue
		}
		name, _, ok := strings.Cut(line, ":")
		name = strings.ToLower(name)
		if !ok || seen[name] || len(seen) >= 32 || name == "transfer-encoding" || name == "expect" {
			return nil, invalid
		}
		seen[name] = true
	}
	req, err := http.ReadRequest(bufio.NewReader(io.MultiReader(bytes.NewReader(header), body)))
	if err != nil {
		return nil, invalid
	}
	if req.Proto != "HTTP/1.1" || req.Host != host || req.Header.Get("Sec-Fetch-Site") == "cross-site" || req.ContentLength < 0 || req.ContentLength > BodyLimit {
		return nil, invalid
	}
	if req.Method == "POST" {
		if req.Header.Get("Origin") != "http://"+host || req.Header.Get("X-ZenQuant-Request") != "1" || req.Header.Get("Content-Type") != "application/json" || req.ContentLength == 0 {
			return nil, invalid
		}
	} else if req.Method != "GET" || req.ContentLength != 0 {
		return nil, invalid
	}
	return req, nil
}
func respond(w io.Writer, status int, mime string, body []byte) {
	fmt.Fprintf(w, "HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %s\r\nConnection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\nContent-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'\r\n\r\n", status, http.StatusText(status), mime, strconv.Itoa(len(body)))
	_, _ = w.Write(body)
}
