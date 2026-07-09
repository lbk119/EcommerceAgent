package auth

import (
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/stretchr/testify/require"
)

func TestTokenManagerIssueAndParse(t *testing.T) {
	manager, err := NewTokenManager("test-secret", time.Hour)
	require.NoError(t, err)

	user := User{
		ID:              "user-1",
		Name:            "Operator",
		TenantIDs:       []string{"tenant-a"},
		DefaultTenantID: "tenant-a",
		ShopIDs:         []string{"shop-a"},
		DefaultShopID:   "shop-a",
		Roles:           []string{"admin"},
	}
	token, expiresIn, err := manager.Issue(user)
	require.NoError(t, err)
	require.NotEmpty(t, token)
	require.Equal(t, int64(3600), expiresIn)

	claims, err := manager.Parse(token)
	require.NoError(t, err)
	require.Equal(t, "user-1", claims.Subject)
	require.Equal(t, "tenant-a", claims.DefaultTenantID)
	require.Equal(t, []string{"shop-a"}, claims.ShopIDs)
	require.Equal(t, []string{"admin"}, claims.Roles)
}

func TestTokenManagerRejectsUnexpectedSigningMethod(t *testing.T) {
	manager, err := NewTokenManager("test-secret", time.Hour)
	require.NoError(t, err)

	token, err := jwt.NewWithClaims(jwt.SigningMethodHS384, Claims{}).SignedString([]byte("test-secret"))
	require.NoError(t, err)

	_, err = manager.Parse(token)
	require.EqualError(t, err, "invalid token")
}

func TestBearerTokenParsing(t *testing.T) {
	token, ok := BearerToken("  bearer   abc.def  ")
	require.True(t, ok)
	require.Equal(t, "abc.def", token)

	_, ok = BearerToken("Basic abc.def")
	require.False(t, ok)

	_, ok = BearerToken("Bearer   ")
	require.False(t, ok)
}
