package middleware

import (
	"DeepAgent/gateway/internal/auth"
	gatewayerrors "DeepAgent/gateway/internal/errors"

	"github.com/gin-gonic/gin"
)

const AuthUserKey = "gateway.auth.user"

// Auth 校验 Bearer JWT，并把网关用户写入 Gin context。
// 这个中间件只回答“用户是谁”，不会解析 Agent 请求体，也不会理解业务任务；
// 后续 Tenant 和 Casbin 中间件会基于这个用户继续做租户解析和 API 授权。
func Auth(tokenManager *auth.TokenManager, userStore auth.UserStore) gin.HandlerFunc {
	return func(c *gin.Context) {
		token, ok := auth.BearerToken(c.GetHeader("Authorization"))
		if !ok {
			// WebSocket 握手通常不能稳定携带自定义 Header，因此兼容 ?token=xxx。
			// 这只是传输方式不同，后续 JWT 校验和租户/Casbin 授权完全一致。
			token = c.Query("token")
			ok = token != ""
		}
		if !ok {
			gatewayerrors.Unauthorized(c, "UNAUTHORIZED", "缺少或非法的 Authorization Bearer token")
			return
		}

		claims, err := tokenManager.Parse(token)
		if err != nil {
			if err.Error() == "token expired" {
				gatewayerrors.Unauthorized(c, "TOKEN_EXPIRED", "登录已过期，请重新登录")
				return
			}
			gatewayerrors.Unauthorized(c, "UNAUTHORIZED", "无效的访问令牌")
			return
		}

		user, found := userStore.FindByID(claims.Subject)
		if !found {
			gatewayerrors.Unauthorized(c, "UNAUTHORIZED", "用户不存在或已被禁用")
			return
		}
		c.Set(AuthUserKey, user)
		c.Next()
	}
}

func CurrentUser(c *gin.Context) (auth.User, bool) {
	value, exists := c.Get(AuthUserKey)
	if !exists {
		return auth.User{}, false
	}
	user, ok := value.(auth.User)
	return user, ok
}
