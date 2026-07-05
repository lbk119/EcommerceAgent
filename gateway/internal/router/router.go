package router

import (
	"net/http"

	"DeepAgent/gateway/internal/config"
	"DeepAgent/gateway/internal/middleware"
	"DeepAgent/gateway/internal/proxy"

	"github.com/gin-gonic/gin"
)

func New(cfg config.Config, brainProxy *proxy.BrainProxy) *gin.Engine {
	engine := gin.New()
	engine.Use(gin.Recovery(), middleware.RequestID(), middleware.CORS())

	engine.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{
			"status": "ok",
			"brain":  cfg.PythonBrainURL.String(),
		})
	})

	registerV1Routes(engine, brainProxy)
	registerCompatibilityRoutes(engine, brainProxy)

	engine.StaticFS("/outputs", http.Dir(cfg.OutputDir))

	return engine
}

func registerV1Routes(engine *gin.Engine, brainProxy *proxy.BrainProxy) {
	v1 := engine.Group("/api/v1")
	v1.POST("/tasks", brainProxy.ServeWithPath("/api/task"))
	v1.GET("/tasks", brainProxy.ServeWithPath("/api/tasks"))
	v1.GET("/tasks/:thread_id", func(c *gin.Context) {
		c.Request.URL.Path = "/api/task/" + c.Param("thread_id")
		brainProxy.Serve(c)
	})
	v1.POST("/tasks/:thread_id/cancel", func(c *gin.Context) {
		c.Request.URL.Path = "/api/task/" + c.Param("thread_id") + "/cancel"
		brainProxy.Serve(c)
	})
	v1.POST("/tasks/:thread_id/resume", func(c *gin.Context) {
		c.Request.URL.Path = "/api/task/" + c.Param("thread_id") + "/resume"
		brainProxy.Serve(c)
	})
	v1.Any("/uploads", brainProxy.ServeWithPath("/api/upload"))
	v1.Any("/files", brainProxy.ServeWithPath("/api/files"))
	v1.Any("/download", brainProxy.ServeWithPath("/api/download"))
	// Tool catalog is read-only metadata for UI/ops; actual tool execution still happens inside DeepAgents.
	v1.GET("/tools/catalog", brainProxy.ServeWithPath("/api/tools/catalog"))
	v1.GET("/traces/:task_id", func(c *gin.Context) {
		c.Request.URL.Path = "/api/traces/" + c.Param("task_id")
		brainProxy.Serve(c)
	})
	v1.GET("/traces/:task_id/timeline", func(c *gin.Context) {
		c.Request.URL.Path = "/api/traces/" + c.Param("task_id") + "/timeline"
		brainProxy.Serve(c)
	})
	v1.GET("/metrics/agents", brainProxy.ServeWithPath("/api/metrics/agents"))
	v1.POST("/memories/search", brainProxy.ServeWithPath("/api/memories/search"))
	v1.GET("/memories/reviews", brainProxy.ServeWithPath("/api/memories/reviews"))
	v1.POST("/memories/reviews/:review_id/approve", func(c *gin.Context) {
		c.Request.URL.Path = "/api/memories/reviews/" + c.Param("review_id") + "/approve"
		brainProxy.Serve(c)
	})
	v1.POST("/memories/reviews/:review_id/reject", func(c *gin.Context) {
		c.Request.URL.Path = "/api/memories/reviews/" + c.Param("review_id") + "/reject"
		brainProxy.Serve(c)
	})
	v1.GET("/policy/proposals", brainProxy.ServeWithPath("/api/policy/proposals"))
	v1.POST("/policy/proposals/:proposal_id/approve", func(c *gin.Context) {
		c.Request.URL.Path = "/api/policy/proposals/" + c.Param("proposal_id") + "/approve"
		brainProxy.Serve(c)
	})
	v1.POST("/policy/proposals/:proposal_id/reject", func(c *gin.Context) {
		c.Request.URL.Path = "/api/policy/proposals/" + c.Param("proposal_id") + "/reject"
		brainProxy.Serve(c)
	})
	v1.Any("/ws/:thread_id", func(c *gin.Context) {
		c.Request.URL.Path = "/ws/" + c.Param("thread_id")
		brainProxy.Serve(c)
	})
}

func registerCompatibilityRoutes(engine *gin.Engine, brainProxy *proxy.BrainProxy) {
	engine.Any("/api/task", brainProxy.Serve)
	engine.Any("/api/tasks", brainProxy.Serve)
	engine.Any("/api/task/:thread_id", brainProxy.Serve)
	engine.Any("/api/task/:thread_id/cancel", brainProxy.Serve)
	engine.Any("/api/task/:thread_id/resume", brainProxy.Serve)
	// Compatibility route for clients that still call the Python Brain path through the gateway.
	engine.Any("/api/tools/catalog", brainProxy.Serve)
	engine.Any("/api/traces/:task_id", brainProxy.Serve)
	engine.Any("/api/traces/:task_id/timeline", brainProxy.Serve)
	engine.Any("/api/metrics/agents", brainProxy.Serve)
	engine.Any("/api/memories/search", brainProxy.Serve)
	engine.Any("/api/memories/reviews", brainProxy.Serve)
	engine.Any("/api/memories/reviews/:review_id/approve", brainProxy.Serve)
	engine.Any("/api/memories/reviews/:review_id/reject", brainProxy.Serve)
	engine.Any("/api/policy/proposals", brainProxy.Serve)
	engine.Any("/api/policy/proposals/:proposal_id/approve", brainProxy.Serve)
	engine.Any("/api/policy/proposals/:proposal_id/reject", brainProxy.Serve)
	engine.Any("/api/upload", brainProxy.Serve)
	engine.Any("/api/files", brainProxy.Serve)
	engine.Any("/api/download", brainProxy.Serve)
	engine.Any("/ws/:thread_id", brainProxy.Serve)
}
