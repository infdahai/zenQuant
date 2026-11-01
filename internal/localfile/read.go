// Package localfile reads bounded local inputs without following symlinks.
package localfile

import (
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"

	"golang.org/x/sys/unix"
)

func absolute(path string) (string, error) {
	// Abs/Clean would erase parents, including symlink/../file. Reject first.
	for _, part := range strings.Split(path, string(filepath.Separator)) {
		if part == ".." {
			return "", errors.New("parent path components are not allowed")
		}
	}
	if path == "" {
		return "", errors.New("empty path")
	}
	return filepath.Abs(path)
}

// OpenDir opens every component relative to its verified parent descriptor.
// The caller owns the returned descriptor.
func OpenDir(path string) (int, error) {
	abs, err := absolute(path)
	if err != nil {
		return -1, err
	}
	fd, err := unix.Open(string(filepath.Separator), unix.O_RDONLY|unix.O_DIRECTORY|unix.O_CLOEXEC, 0)
	if err != nil {
		return -1, err
	}
	for _, part := range strings.Split(strings.TrimPrefix(abs, string(filepath.Separator)), string(filepath.Separator)) {
		if part == "" {
			continue
		}
		next, err := unix.Openat(fd, part, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
		unix.Close(fd)
		if err != nil {
			return -1, err
		}
		fd = next
	}
	return fd, nil
}

// Read reads at most limit bytes from a regular file. O_NONBLOCK prevents an
// untrusted FIFO from blocking before its type can be checked with fstat.
func Read(path string, limit int64) ([]byte, error) {
	abs, err := absolute(path)
	if err != nil {
		return nil, err
	}
	parent, err := OpenDir(filepath.Dir(abs))
	if err != nil {
		return nil, err
	}
	defer unix.Close(parent)
	fd, err := unix.Openat(parent, filepath.Base(abs), unix.O_RDONLY|unix.O_NOFOLLOW|unix.O_NONBLOCK|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	f := os.NewFile(uintptr(fd), path)
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() {
		return nil, errors.New("input must be a regular file")
	}
	if info.Size() > limit {
		return nil, errors.New("input exceeds limit")
	}
	data, err := io.ReadAll(io.LimitReader(f, limit+1))
	if err != nil {
		return nil, err
	}
	if int64(len(data)) > limit {
		return nil, errors.New("input exceeds limit")
	}
	return data, nil
}
