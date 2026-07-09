package middleware

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"DeepAgent/gateway/internal/auth"

	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

type fakeUserStore struct {
	user  auth.User
	found bool
}

func (s fakeUserStore) Authenticate(username string, password string) (auth.User, error) {
	return s.user, nil
}
func (s fakeUserStore) FindByID(userID string) (auth.User, bool)             { return s.user, s.found }
func (s fakeUserStore) Register(input auth.RegisterInput) (auth.User, error) { return s.user, nil }
func (s fakeUserStore) AddShop(userID string, tenantID string, shopID string) (auth.User, error) {
	return s.user, nil
}
func (s fakeUserStore) UpdateProfile(userID string, input auth.ProfileInput) (auth.User, error) {
	return s.user, nil
}
func (s fakeUserStore) UpdatePassword(userID string, currentPassword string, newPassword string) error {
	return nil
}
func (s fakeUserStore) SetOnboardingCompleted(userID string, completed bool) (auth.User, error) {
	return s.user, nil
}
func (s fakeUserStore) Backend() auth.StoreBackendInfo {
	return auth.StoreBackendInfo{Backend: "memory"}
}

func TestAuthMissingBearerReturnsGatewayEnvelope(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	manager, err := auth.NewTokenManager("test-secret", time.Hour)
	require.NoError(t, err)
	router.Use(Auth(manager, fakeUserStore{}))
	router.GET("/protected", func(c *gin.Context) { c.Status(http.StatusNoContent) })

	request := httptest.NewRequest(http.MethodGet, "/protected", nil)
	recorder := httptest.NewRecorder()
	router.ServeHTTP(recorder, request)

	require.Equal(t, http.StatusUnauthorized, recorder.Code)
	var body map[string]any
	require.NoError(t, json.Unmarshal(recorder.Body.Bytes(), &body))
	require.Equal(t, false, body["success"])
	require.Equal(t, "UNAUTHORIZED", body["error"].(map[string]any)["code"])
}

func TestTenantRejectsForbiddenShop(t *testing.T) {
	gin.SetMode(gin.TestMode)
	user := auth.User{ID: "user-1", TenantIDs: []string{"tenant-a"}, DefaultTenantID: "tenant-a", ShopIDs: []string{"shop-a"}, DefaultShopID: "shop-a"}
	router := gin.New()
	router.Use(func(c *gin.Context) {
		c.Set(AuthUserKey, user)
		c.Next()
	}, Tenant())
	router.GET("/tenant", func(c *gin.Context) { c.Status(http.StatusNoContent) })

	request := httptest.NewRequest(http.MethodGet, "/tenant", nil)
	request.Header.Set("X-Shop-ID", "shop-b")
	recorder := httptest.NewRecorder()
	router.ServeHTTP(recorder, request)

	require.Equal(t, http.StatusForbidden, recorder.Code)
	var body map[string]any
	require.NoError(t, json.Unmarshal(recorder.Body.Bytes(), &body))
	require.Equal(t, "TENANT_FORBIDDEN", body["error"].(map[string]any)["code"])
}
