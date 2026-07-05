package authorization

import (
	"net/http"

	gatewayerrors "DeepAgent/gateway/internal/errors"
	"DeepAgent/gateway/internal/middleware"

	"github.com/casbin/casbin/v2"
	"github.com/gin-gonic/gin"
)

// CasbinAuthorize 在租户解析之后执行 API 级授权。
// 授权输入固定为：sub=user_id, dom=tenant_id, obj=Gin 路由模式, act=HTTP method。
// 这样 Gateway 只理解平台资源和 HTTP 动作，不理解 Agent 内部业务语义；Python ToolRegistry
// 仍负责工具级二次权限，避免 Go 与 Python 各自维护一套工具权限规则。
func CasbinAuthorize(enforcer *casbin.Enforcer) gin.HandlerFunc {
	return func(c *gin.Context) {
		tenantContext, ok := middleware.CurrentTenant(c)
		if !ok {
			gatewayerrors.Unauthorized(c, "UNAUTHORIZED", "缺少租户上下文，无法执行授权")
			return
		}

		object := c.FullPath()
		if object == "" {
			// Gin 未命中具名路由时 FullPath 为空，例如静态文件或未来新增的透传路由。
			// fallback 使用真实 URL，至少保证不会因为空资源名而误放行。
			object = c.Request.URL.Path
		}
		action := c.Request.Method

		allowed, err := enforcer.Enforce(tenantContext.UserID, tenantContext.TenantID, object, action)
		if err != nil {
			gatewayerrors.Abort(c, http.StatusInternalServerError, "AUTHORIZATION_ERROR", "权限策略执行失败", gin.H{
				"object": object,
				"action": action,
			})
			return
		}
		if !allowed {
			gatewayerrors.Forbidden(c, "FORBIDDEN", "当前用户无权访问该 API 资源")
			return
		}

		c.Next()
	}
}
