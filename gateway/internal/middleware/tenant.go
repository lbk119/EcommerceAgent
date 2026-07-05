package middleware

import (
	"strings"

	gatewayerrors "DeepAgent/gateway/internal/errors"
	"DeepAgent/gateway/internal/tenant"

	"github.com/gin-gonic/gin"
)

const TenantContextKey = "gateway.tenant.context"

// Tenant 在认证之后解析可信租户和店铺上下文。
// 客户端可以通过 X-Tenant-ID / X-Shop-ID 或 WebSocket query 指定目标租户/店铺，
// 但网关会先校验它们是否属于当前用户，校验通过后才允许请求继续进入 Casbin 和 Brain。
func Tenant() gin.HandlerFunc {
	return func(c *gin.Context) {
		user, ok := CurrentUser(c)
		if !ok {
			gatewayerrors.Unauthorized(c, "UNAUTHORIZED", "缺少认证用户上下文")
			return
		}

		requestedTenantID := c.GetHeader("X-Tenant-ID")
		if requestedTenantID == "" {
			requestedTenantID = c.Query("tenant_id")
		}
		requestedShopID := c.GetHeader("X-Shop-ID")
		if requestedShopID == "" {
			requestedShopID = c.Query("shop_id")
		}

		context, err := tenant.Resolve(user, requestedTenantID, requestedShopID)
		if err != nil {
			switch err.Error() {
			case "tenant required":
				gatewayerrors.Abort(c, 400, "TENANT_REQUIRED", "缺少租户上下文", nil)
			case "tenant forbidden":
				gatewayerrors.Forbidden(c, "TENANT_FORBIDDEN", "当前用户无权访问该租户")
			case "shop forbidden":
				gatewayerrors.Forbidden(c, "TENANT_FORBIDDEN", "当前用户无权访问该店铺")
			default:
				gatewayerrors.Forbidden(c, "TENANT_FORBIDDEN", "租户上下文校验失败")
			}
			return
		}

		c.Set(TenantContextKey, context)
		c.Next()
	}
}

func CurrentTenant(c *gin.Context) (tenant.TenantContext, bool) {
	value, exists := c.Get(TenantContextKey)
	if !exists {
		return tenant.TenantContext{}, false
	}
	context, ok := value.(tenant.TenantContext)
	return context, ok
}

func JoinContextValues(values []string) string {
	return strings.Join(values, ",")
}
