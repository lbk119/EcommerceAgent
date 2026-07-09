package router

import (
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"

	"DeepAgent/gateway/internal/auth"
	"DeepAgent/gateway/internal/config"
	"DeepAgent/gateway/internal/handlers"
	"DeepAgent/gateway/internal/proxy"

	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestHealthContract(t *testing.T) {
	gin.SetMode(gin.TestMode)
	brainURL, err := url.Parse("http://brain.test")
	require.NoError(t, err)
	store := auth.NewStaticUserStore()
	tokenManager, err := auth.NewTokenManager("test-secret", time.Hour)
	require.NoError(t, err)
	authHandler := handlers.NewAuthHandler(store, tokenManager, nil)
	engine := New(config.Config{PythonBrainURL: brainURL, AuthEnabled: false}, proxy.NewBrainProxy(brainURL), authHandler, tokenManager, store, nil)

	request := httptest.NewRequest(http.MethodGet, "/health", nil)
	recorder := httptest.NewRecorder()
	engine.ServeHTTP(recorder, request)

	require.Equal(t, http.StatusOK, recorder.Code)
	require.JSONEq(t, `{"status":"ok","brain":"http://brain.test","user_store_backend":"memory","mysql_database":""}`, recorder.Body.String())
}
