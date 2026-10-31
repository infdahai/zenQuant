package cli

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
)

// CheckoutRoot locates the checkout containing start, including linked worktrees.
func CheckoutRoot(start string) (string, error) {
	dir, err := filepath.Abs(start)
	if err != nil {
		return "", err
	}
	for {
		_, err := os.Lstat(filepath.Join(dir, ".git"))
		if err == nil {
			return dir, nil
		}
		if !os.IsNotExist(err) {
			return "", err
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", errors.New("run from a repository checkout")
		}
		dir = parent
	}
}

// RepositoryRoot resolves linked worktrees to the single repository allocation authority.
// It reads only Git location metadata and never invokes Git or a legacy backend.
func RepositoryRoot(start string) (string, error) {
	dir, err := filepath.Abs(start)
	if err != nil {
		return "", err
	}
	for {
		entry := filepath.Join(dir, ".git")
		info, err := os.Lstat(entry)
		if err == nil {
			if info.IsDir() {
				return dir, nil
			}
			if !info.Mode().IsRegular() {
				return "", errors.New("invalid git location")
			}
			b, err := os.ReadFile(entry)
			if err != nil {
				return "", err
			}
			if !strings.HasPrefix(string(b), "gitdir: ") {
				return "", errors.New("invalid gitdir")
			}
			gitdir := strings.TrimSpace(strings.TrimPrefix(string(b), "gitdir: "))
			if !filepath.IsAbs(gitdir) {
				gitdir = filepath.Join(dir, gitdir)
			}
			common, err := os.ReadFile(filepath.Join(gitdir, "commondir"))
			if err != nil {
				return "", err
			}
			path := strings.TrimSpace(string(common))
			if path == "" {
				return "", errors.New("empty commondir")
			}
			if !filepath.IsAbs(path) {
				path = filepath.Join(gitdir, path)
			}
			path, err = filepath.EvalSymlinks(path)
			if err != nil {
				return "", err
			}
			info, err = os.Stat(path)
			if err != nil {
				return "", err
			}
			if !info.IsDir() {
				return "", errors.New("invalid common directory")
			}
			return filepath.Dir(path), nil
		}
		if !os.IsNotExist(err) {
			return "", err
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", errors.New("run from a repository checkout")
		}
		dir = parent
	}
}
