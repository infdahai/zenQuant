package server

import (
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func exchange(t *testing.T, change func(string) string) string {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	host := listener.Addr().String()
	done := make(chan struct{})
	s := Server{Options: func(_ string, b []byte) ([]byte, error) { return b, nil }}
	go func() {
		defer close(done)
		conn, err := listener.Accept()
		if err == nil {
			s.handle(conn, host)
		}
	}()
	conn, err := net.Dial("tcp", host)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, err = io.WriteString(conn, change(host)); err != nil {
		t.Fatal(err)
	}
	b, err := io.ReadAll(conn)
	if err != nil {
		t.Fatal(err)
	}
	<-done
	return string(b)
}
func post(host string) string {
	return fmt.Sprintf("POST /api/curve HTTP/1.1\r\nHost: %s\r\nOrigin: http://%s\r\nX-ZenQuant-Request: 1\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}", host, host)
}
func TestHTTPAdmission(t *testing.T) {
	for _, tc := range []struct{ name, old, new string }{
		{"origin", "Origin: http://", "Origin: https://"},
		{"host", "Host: ", "Host: evil"},
		{"duplicate length", "Content-Length: 2", "Content-Length: 2\r\nContent-Length: 2"},
		{"chunked", "Content-Length: 2", "Transfer-Encoding: chunked\r\nContent-Length: 2"},
		{"oversize", "Content-Length: 2", "Content-Length: 1048577"},
		{"missing guard", "X-ZenQuant-Request: 1\r\n", ""},
		{"expect", "Content-Length: 2", "Expect: 100-continue\r\nContent-Length: 2"},
		{"cross site", "Content-Length: 2", "Sec-Fetch-Site: cross-site\r\nContent-Length: 2"},
		{"duplicate origin", "Origin: http://", "Origin: http://evil\r\nOrigin: http://"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := exchange(t, func(h string) string { return strings.Replace(post(h), tc.old, tc.new, 1) })
			if !strings.HasPrefix(got, "HTTP/1.1 400 ") {
				t.Fatal(got)
			}
		})
	}
	got := exchange(t, post)
	if !strings.HasPrefix(got, "HTTP/1.1 200 ") || !strings.HasSuffix(got, "{}") {
		t.Fatal(got)
	}
	if !strings.Contains(got, "Content-Security-Policy:") {
		t.Fatal("missing CSP")
	}
	got = exchange(t, func(h string) string { return fmt.Sprintf("GET /../../.env HTTP/1.1\r\nHost: %s\r\n\r\n", h) })
	if !strings.HasPrefix(got, "HTTP/1.1 404 ") {
		t.Fatal(got)
	}
}
func TestStaticBundle(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "index.html"), []byte("safe"), 0600); err != nil {
		t.Fatal(err)
	}
	assets, err := LoadAssets(dir)
	if err != nil {
		t.Fatal(err)
	}
	if string(assets["/paper"].Body) != "safe" {
		t.Fatal(assets)
	}
	if err := os.Symlink(filepath.Join(dir, "index.html"), filepath.Join(dir, "leak.js")); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadAssets(dir); err == nil {
		t.Fatal("symlink admitted")
	}
}
func TestWorkersStopOnListenerClose(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- (&Server{}).Serve(listener) }()
	listener.Close()
	if err := <-done; err != nil {
		t.Fatal(err)
	}
}
