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