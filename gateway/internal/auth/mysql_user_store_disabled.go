//go:build !mysql

package auth

import "errors"

func newMySQLUserStoreFromEnv() (UserStore, error) {
	return nil, errors.New("mysql user store is not compiled; run `go get github.com/go-sql-driver/mysql` and build with `-tags mysql`")
}
