package proxy

import (
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"

	"DeepAgent/gateway/internal/middleware"
	"DeepAgent/gateway/internal/tenant"

	"github.com/gin-gonic/gin"
	"github.com/h2non/gock"
	"github.com/stretchr/testify/require"
)

type closeNotifyRecorder struct {
	*httptest.ResponseRecorder
	closed chan bool
}

func (r *closeNotifyRecorder) CloseNotify() <-chan bool {
	return r.closed
}

func TestBrainProxyInjectsTrustedTenantHeaders(t *testing.T) {
	gin.SetMode(gin.TestMode)
	defer gock.Off()

	gock.New("http://brain.test").
		Get("/api/workspace").
		MatchHeader("X-User-ID", "user-1").
		MatchHeader("X-Tenant-ID", "tenant-a").
		MatchHeader("X-Shop-ID", "shop-a").
		MatchHeader("X-User-Role", "admin").
		MatchHeader("X-Permissions", "task:read,trace:read").
		Reply(http.StatusOK).
		JSON(map[string]any{"ok": true})

	backend, err := url.Parse("http://brain.test")
	require.NoError(t, err)
	router := gin.New()
	router.Use(func(c *gin.Context) {
		c.Set(middleware.TenantContextKey, tenant.TenantContext{
			TenantID:    "tenant-a",
			UserID:      "user-1",
			ShopID:      "shop-a",
			Roles:       []string{"admin"},
			Permissions: []string{"task:read", "trace:read"},
		})
		c.Next()
	})
	router.GET("/workspace", NewBrainProxy(backend).ServeWithPath("/api/workspace"))

	request := httptest.NewRequest(http.MethodGet, "/workspace", nil)
	request.Header.Set("X-Tenant-ID", "attacker-tenant")
	request.Header.Set("X-Shop-ID", "attacker-shop")
	recorder := &closeNotifyRecorder{ResponseRecorder: httptest.NewRecorder(), closed: make(chan bool, 1)}
	router.ServeHTTP(recorder, request)

	require.Equal(t, http.StatusOK, recorder.Code)
	require.True(t, gock.IsDone())
}
