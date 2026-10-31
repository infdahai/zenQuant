//go:build darwin

package paper

import "golang.org/x/sys/unix"

func renameExclusive(fromDir int, from string, toDir int, to string) error {
	return unix.RenameatxNp(fromDir, from, toDir, to, unix.RENAME_EXCL)
}
