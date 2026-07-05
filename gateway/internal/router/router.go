package router

import (
	"net/http"

	"DeepAgent/gateway/internal/auth"
	"DeepAgent/gateway/internal/authorization"
	"DeepAgent/gateway/internal/config"
	"DeepAgent/gateway/internal/handlers"
	"DeepAgent/gateway/internal/middleware"
	"DeepAgent/gateway/internal/proxy"

	"github.com/casbin/casbin/v2"
	"github.com/gin-gonic/gin"
)

func New(cfg config.Config, brainProxy *proxy.BrainProxy, authHandler *handlers.AuthHandler, tokenManager *auth.TokenManager, userStore auth.UserStore, enforcer *casbin.Enforcer) *gin.Engine {
	engine := gin.New()
	engine.Use(gin.Recovery(), middleware.RequestID(), middleware.CORS())

	engine.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{
			"status": "ok",
			"brain":  cfg.PythonBrainURL.String(),
		})
	})

	registerAuthRoutes(engine, authHandler, tokenManager, userStore, enforcer, cfg.AuthEnabled)
	registerV1Routes(engine, brainProxy, tokenManager, userStore, enforcer, cfg.AuthEnabled)

	// 生产阶段不再通过 /outputs 暴露静态目录。
	// 生成文件统一走 /api/v1/download，由网关完成 API 授权，再由 Python Brain 按会话/租户校验文件归属。
	// 这样即使攻击者猜到 output/session_xxx/xxx 文件路径，也不能绕过身份和租户边界直接下载。

	return engine
}

func registerAuthRoutes(engine *gin.Engine, authHandler *handlers.AuthHandler, tokenManager *auth.TokenManager, userStore auth.UserStore, enforcer *casbin.Enforcer, authEnabled bool) {
	v1 := engine.Group("/api/v1")
	v1.POST("/auth/login", authHandler.Login)

	// me/logout 需要有效 token：前端可以用 me 确认当前 token 是否仍能解析为有效用户和租户上下文。
	authenticated := v1.Group("/auth", protectedMiddleware(tokenManager, userStore, enforcer, authEnabled)...)
	authenticated.GET("/me", authHandler.Me)
	authenticated.POST("/logout", authHandler.Logout)
}

func registerV1Routes(engine *gin.Engine, brainProxy *proxy.BrainProxy, tokenManager *auth.TokenManager, userStore auth.UserStore, enforcer *casbin.Enforcer, authEnabled bool) {
	v1 := engine.Group("/api/v1")
	// 所有面向 Python Brain 的路由都经过 Auth -> Tenant -> Casbin。
	// Go 网关只做身份、租户和 API 资源治理；Agent 业务执行仍全部留在 Python Brain。
	v1.Use(protectedMiddleware(tokenManager, userStore, enforcer, authEnabled)...)
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
	// 工具目录只是给前端/运维展示 metadata；真实工具执行仍发生在 Python DeepAgents 内部。
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

func protectedMiddleware(tokenManager *auth.TokenManager, userStore auth.UserStore, enforcer *casbin.Enforcer, authEnabled bool) []gin.HandlerFunc {
	if !authEnabled {
		// 本地排障时可通过 GATEWAY_AUTH_ENABLED=false 关闭网关鉴权链。
		// 生产环境应保持开启，让 Python Brain 只信任网关注入的上下文。
		return nil
	}
	return []gin.HandlerFunc{
		middleware.Auth(tokenManager, userStore),
		middleware.Tenant(),
		authorization.CasbinAuthorize(enforcer),
	}
}
