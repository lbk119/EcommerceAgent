package proxy

import (
	"context"
	"io"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
	"time"

	"DeepAgent/gateway/internal/middleware"

	"github.com/gin-gonic/gin"
)

type BrainProxy struct {
	backend *url.URL
	proxy   *httputil.ReverseProxy
}

func NewBrainProxy(backend *url.URL) *BrainProxy {
	reverseProxy := httputil.NewSingleHostReverseProxy(backend)
	originalDirector := reverseProxy.Director
	reverseProxy.Director = func(request *http.Request) {
		originalDirector(request)
		request.Host = backend.Host
		if requestID := request.Header.Get(middleware.RequestIDHeader); requestID != "" {
			request.Header.Set(middleware.RequestIDHeader, requestID)
		}
	}

	return &BrainProxy{backend: backend, proxy: reverseProxy}
}

func (p *BrainProxy) Serve(c *gin.Context) {
	p.injectTrustedHeaders(c)
	p.proxy.ServeHTTP(c.Writer, c.Request)
}

func (p *BrainProxy) injectTrustedHeaders(c *gin.Context) {
	if tenantContext, ok := middleware.CurrentTenant(c); ok {
		// 这些 Header 由网关统一覆盖写入，即使浏览器伪造同名 Header 也会在这里被替换。
		// Python Brain 只信任这些 Header，并把 body 里的身份字段当作本地直连调试的兜底值。
		c.Request.Header.Set("X-User-ID", tenantContext.UserID)
		c.Request.Header.Set("X-Tenant-ID", tenantContext.TenantID)
		c.Request.Header.Set("X-Shop-ID", tenantContext.ShopID)
		c.Request.Header.Set("X-User-Role", middleware.JoinContextValues(tenantContext.Roles))
		c.Request.Header.Set("X-Permissions", middleware.JoinContextValues(tenantContext.Permissions))
		c.Request.Header.Set("X-Auth-Source", "gateway")
	}
}

func (p *BrainProxy) ServeWithPath(path string) gin.HandlerFunc {
	return func(c *gin.Context) {
		c.Request.URL.Path = path
		p.Serve(c)
	}
}

func (p *BrainProxy) ServeDiagnosticWithPath(path string, timeout time.Duration) gin.HandlerFunc {
	return func(c *gin.Context) {
		p.injectTrustedHeaders(c)
		target := *p.backend
		target.Path = path
		target.RawQuery = c.Request.URL.RawQuery
		ctx, cancel := context.WithTimeout(c.Request.Context(), timeout)
		defer cancel()
		request, err := http.NewRequestWithContext(ctx, c.Request.Method, target.String(), nil)
		if err != nil {
			c.JSON(http.StatusOK, diagnosticFallback("invalid diagnostic request"))
			return
		}
		request.Header = c.Request.Header.Clone()
		response, err := (&http.Client{Timeout: timeout}).Do(request)
		if err != nil {
			c.JSON(http.StatusOK, diagnosticFallback("diagnostic upstream timed out or failed"))
			return
		}
		defer response.Body.Close()
		for key, values := range response.Header {
			for _, value := range values {
				c.Writer.Header().Add(key, value)
			}
		}
		c.Status(response.StatusCode)
		_, _ = io.Copy(c.Writer, response.Body)
	}
}

func diagnosticFallback(message string) gin.H {
	return gin.H{"tasks": []any{}, "diagnostic": gin.H{"source": "gateway_bounded_proxy", "degraded": true, "message": message}}
}

func (p *BrainProxy) ServeWithPrefixTrim(prefix string) gin.HandlerFunc {
	return func(c *gin.Context) {
		trimmedPath := strings.TrimPrefix(c.Request.URL.Path, prefix)
		if trimmedPath == "" {
			trimmedPath = "/"
		}
		c.Request.URL.Path = trimmedPath
		p.Serve(c)
	}
}

func (p *BrainProxy) ServeWithPrefixReplace(prefix string, replacement string) gin.HandlerFunc {
	return func(c *gin.Context) {
		trimmedPath := strings.TrimPrefix(c.Request.URL.Path, prefix)
		if trimmedPath == "" {
			trimmedPath = "/"
		}
		c.Request.URL.Path = strings.TrimRight(replacement, "/") + trimmedPath
		p.Serve(c)
	}
}
