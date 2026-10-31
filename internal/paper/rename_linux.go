//go:build linux

package paper

import "golang.org/x/sys/unix"

func renameExclusive(fromDir int, from string, toDir int, to string) error {
	return unix.Renameat2(fromDir, from, toDir, to, unix.RENAME_NOREPLACE)
}
