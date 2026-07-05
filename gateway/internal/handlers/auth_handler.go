package handlers

import (
	"net/http"

	"DeepAgent/gateway/internal/auth"
	gatewayerrors "DeepAgent/gateway/internal/errors"
	"DeepAgent/gateway/internal/middleware"

	"github.com/gin-gonic/gin"
)

type AuthHandler struct {
	store        auth.UserStore
	tokenManager *auth.TokenManager
}

type LoginRequest struct {
	Username string `json:"username" binding:"required"`
	Password string `json:"password" binding:"required"`
}

func NewAuthHandler(store auth.UserStore, tokenManager *auth.TokenManager) *AuthHandler {
	return &AuthHandler{store: store, tokenManager: tokenManager}
}

func (h *AuthHandler) Login(c *gin.Context) {
	var request LoginRequest
	if err := c.ShouldBindJSON(&request); err != nil {
		gatewayerrors.Abort(c, http.StatusBadRequest, "INVALID_REQUEST", "请输入用户名和密码", nil)
		return
	}

	user, err := h.store.Authenticate(request.Username, request.Password)
	if err != nil {
		gatewayerrors.Unauthorized(c, "UNAUTHORIZED", "用户名或密码错误")
		return
	}

	token, expiresIn, err := h.tokenManager.Issue(user)
	if err != nil {
		gatewayerrors.Abort(c, http.StatusInternalServerError, "INTERNAL_ERROR", "签发访问令牌失败", nil)
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"access_token": token,
		"token_type":   "Bearer",
		"expires_in":   expiresIn,
		"user": gin.H{
			"id":                user.ID,
			"name":              user.Name,
			"default_tenant_id": user.DefaultTenantID,
			"default_shop_id":   user.DefaultShopID,
			"roles":             user.Roles,
			"permissions":       user.Permissions,
		},
	})
}

func (h *AuthHandler) Me(c *gin.Context) {
	user, ok := middleware.CurrentUser(c)
	if !ok {
		gatewayerrors.Unauthorized(c, "UNAUTHORIZED", "缺少认证用户上下文")
		return
	}
	context, _ := middleware.CurrentTenant(c)
	c.JSON(http.StatusOK, gin.H{
		"user": gin.H{
			"id":                user.ID,
			"name":              user.Name,
			"default_tenant_id": user.DefaultTenantID,
			"default_shop_id":   user.DefaultShopID,
			"roles":             user.Roles,
			"permissions":       user.Permissions,
		},
		"tenant_context": context,
	})
}

func (h *AuthHandler) Logout(c *gin.Context) {
	// Local JWT logout is stateless in phase 1. Clients should delete the token.
	// A server-side denylist can be added later when Redis/session storage is introduced.
	c.JSON(http.StatusOK, gin.H{"status": "logged_out"})
}
