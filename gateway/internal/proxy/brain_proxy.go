package proxy

import (
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"

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
	p.proxy.ServeHTTP(c.Writer, c.Request)
}

func (p *BrainProxy) ServeWithPath(path string) gin.HandlerFunc {
	return func(c *gin.Context) {
		c.Request.URL.Path = path
		p.Serve(c)
	}
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
