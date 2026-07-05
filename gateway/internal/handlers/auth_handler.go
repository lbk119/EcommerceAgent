package handlers

import (
	"net/http"
	"strings"

	"DeepAgent/gateway/internal/auth"
	gatewayerrors "DeepAgent/gateway/internal/errors"
	"DeepAgent/gateway/internal/middleware"

	"github.com/casbin/casbin/v2"
	"github.com/gin-gonic/gin"
)

type AuthHandler struct {
	store        auth.UserStore
	tokenManager *auth.TokenManager
	enforcer     *casbin.Enforcer
}

type LoginRequest struct {
	Account  string `json:"account"`
	Username string `json:"username"`
	Password string `json:"password" binding:"required"`
}

type RegisterRequest struct {
	Username        string `json:"username"`
	Account         string `json:"account"`
	Name            string `json:"name"`
	Email           string `json:"email"`
	Phone           string `json:"phone"`
	CompanyName     string `json:"companyName"`
	ConfirmPassword string `json:"confirmPassword"`
	Password        string `json:"password" binding:"required"`
	TenantID        string `json:"tenant_id"`
	TenantName      string `json:"tenant_name"`
	TenantNameCamel string `json:"tenantName"`
	ShopID          string `json:"shop_id"`
	ShopName        string `json:"shop_name"`
}

type CreateShopRequest struct {
	TenantID string `json:"tenant_id"`
	ShopID   string `json:"shop_id" binding:"required"`
	ShopName string `json:"shop_name"`
}

type UpdateProfileRequest struct {
	Name        string `json:"name"`
	Email       string `json:"email"`
	Phone       string `json:"phone"`
	CompanyName string `json:"companyName"`
}

type UpdatePasswordRequest struct {
	CurrentPassword string `json:"currentPassword" binding:"required"`
	NewPassword     string `json:"newPassword" binding:"required"`
}

func NewAuthHandler(store auth.UserStore, tokenManager *auth.TokenManager, enforcer *casbin.Enforcer) *AuthHandler {
	return &AuthHandler{store: store, tokenManager: tokenManager, enforcer: enforcer}
}

func (h *AuthHandler) Register(c *gin.Context) {
	var request RegisterRequest
	if err := c.ShouldBindJSON(&request); err != nil {
		gatewayerrors.Abort(c, http.StatusBadRequest, "INVALID_REQUEST", "请输入用户名和密码", nil)
		return
	}
	if request.ConfirmPassword != "" && request.ConfirmPassword != request.Password {
		gatewayerrors.Abort(c, http.StatusBadRequest, "INVALID_REQUEST", "两次输入的密码不一致", nil)
		return
	}
	account := firstNonEmpty(request.Username, request.Account, request.Email)
	tenantName := firstNonEmpty(request.TenantName, request.TenantNameCamel, request.CompanyName)

	user, err := h.store.Register(auth.RegisterInput{
		UserID:      account,
		Name:        request.Name,
		Email:       request.Email,
		Phone:       request.Phone,
		CompanyName: request.CompanyName,
		Password:    request.Password,
		TenantID:    request.TenantID,
		TenantName:  tenantName,
		ShopID:      request.ShopID,
		ShopName:    request.ShopName,
	})
	if err != nil {
		status := http.StatusBadRequest
		if err.Error() == "user already exists" {
			status = http.StatusConflict
		}
		gatewayerrors.Abort(c, status, "REGISTER_FAILED", registerErrorMessage(err.Error()), nil)
		return
	}
	h.ensureTenantAdminPolicy(user)
	h.respondWithToken(c, user)
}

func (h *AuthHandler) CreateShop(c *gin.Context) {
	currentUser, ok := middleware.CurrentUser(c)
	if !ok {
		gatewayerrors.Unauthorized(c, "UNAUTHORIZED", "缺少认证用户上下文")
		return
	}
	var request CreateShopRequest
	if err := c.ShouldBindJSON(&request); err != nil {
		gatewayerrors.Abort(c, http.StatusBadRequest, "INVALID_REQUEST", "请输入店铺 ID", nil)
		return
	}
	user, err := h.store.AddShop(currentUser.ID, request.TenantID, firstNonEmpty(request.ShopID, request.ShopName))
	if err != nil {
		gatewayerrors.Abort(c, http.StatusBadRequest, "CREATE_SHOP_FAILED", shopErrorMessage(err.Error()), nil)
		return
	}
	h.ensureTenantAdminPolicy(user)
	h.respondWithToken(c, user)
}
func (h *AuthHandler) Login(c *gin.Context) {
	var request LoginRequest
	if err := c.ShouldBindJSON(&request); err != nil {
		gatewayerrors.Abort(c, http.StatusBadRequest, "INVALID_REQUEST", "请输入用户名和密码", nil)
		return
	}
	account := firstNonEmpty(request.Account, request.Username)
	if strings.TrimSpace(account) == "" {
		gatewayerrors.Abort(c, http.StatusBadRequest, "INVALID_REQUEST", "请输入账号和密码", nil)
		return
	}

	user, err := h.store.Authenticate(account, request.Password)
	if err != nil {
		gatewayerrors.Unauthorized(c, "UNAUTHORIZED", "用户名或密码错误")
		return
	}

	h.ensureTenantAdminPolicy(user)
	h.respondWithToken(c, user)
}

func (h *AuthHandler) respondWithToken(c *gin.Context, user auth.User) {
	token, expiresIn, err := h.tokenManager.Issue(user)
	if err != nil {
		gatewayerrors.Abort(c, http.StatusInternalServerError, "INTERNAL_ERROR", "签发访问令牌失败", nil)
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"access_token": token,
		"accessToken":  token,
		"token_type":   "Bearer",
		"tokenType":    "Bearer",
		"expires_in":   expiresIn,
		"expiresIn":    expiresIn,
		"user":         userPayload(user),
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
		"user":           userPayload(user),
		"tenant_context": context,
		"tenantContext":  context,
	})
}

func (h *AuthHandler) UpdateProfile(c *gin.Context) {
	user, ok := middleware.CurrentUser(c)
	if !ok {
		gatewayerrors.Unauthorized(c, "UNAUTHORIZED", "缺少认证用户上下文")
		return
	}
	var request UpdateProfileRequest
	if err := c.ShouldBindJSON(&request); err != nil {
		gatewayerrors.Abort(c, http.StatusBadRequest, "INVALID_REQUEST", "资料格式不正确", nil)
		return
	}
	updated, err := h.store.UpdateProfile(user.ID, auth.ProfileInput{Name: request.Name, Email: request.Email, Phone: request.Phone, CompanyName: request.CompanyName})
	if err != nil {
		gatewayerrors.Abort(c, http.StatusBadRequest, "UPDATE_PROFILE_FAILED", "更新资料失败", nil)
		return
	}
	c.JSON(http.StatusOK, gin.H{"user": userPayload(updated)})
}

func (h *AuthHandler) UpdatePassword(c *gin.Context) {
	user, ok := middleware.CurrentUser(c)
	if !ok {
		gatewayerrors.Unauthorized(c, "UNAUTHORIZED", "缺少认证用户上下文")
		return
	}
	var request UpdatePasswordRequest
	if err := c.ShouldBindJSON(&request); err != nil {
		gatewayerrors.Abort(c, http.StatusBadRequest, "INVALID_REQUEST", "请输入当前密码和新密码", nil)
		return
	}
	if err := h.store.UpdatePassword(user.ID, request.CurrentPassword, request.NewPassword); err != nil {
		gatewayerrors.Abort(c, http.StatusBadRequest, "UPDATE_PASSWORD_FAILED", "密码更新失败，请检查当前密码", nil)
		return
	}
	c.JSON(http.StatusOK, gin.H{"status": "updated"})
}

func (h *AuthHandler) MarkOnboardingCompleted(c *gin.Context) {
	user, ok := middleware.CurrentUser(c)
	if !ok {
		gatewayerrors.Unauthorized(c, "UNAUTHORIZED", "缺少认证用户上下文")
		return
	}
	updated, err := h.store.SetOnboardingCompleted(user.ID, true)
	if err != nil {
		gatewayerrors.Abort(c, http.StatusBadRequest, "UPDATE_ONBOARDING_FAILED", "更新引导状态失败", nil)
		return
	}
	c.JSON(http.StatusOK, gin.H{"user": userPayload(updated)})
}

func (h *AuthHandler) Logout(c *gin.Context) {
	// Local JWT logout is stateless in phase 1. Clients should delete the token.
	// A server-side denylist can be added later when Redis/session storage is introduced.
	c.JSON(http.StatusOK, gin.H{"status": "logged_out"})
}

func userPayload(user auth.User) gin.H {
	role := "admin"
	if len(user.Roles) > 0 {
		role = user.Roles[0]
	}
	companyName := firstNonEmpty(user.CompanyName, user.DefaultTenantID, "默认组织")
	return gin.H{
		"id":                   user.ID,
		"name":                 user.Name,
		"email":                firstNonEmpty(user.Email, user.ID),
		"phone":                user.Phone,
		"companyName":          companyName,
		"company_name":         companyName,
		"role":                 role,
		"plan":                 firstNonEmpty(user.Plan, "团队版"),
		"createdAt":            firstNonEmpty(user.CreatedAt, "刚刚"),
		"created_at":           firstNonEmpty(user.CreatedAt, "刚刚"),
		"onboardingCompleted":  user.OnboardingDone,
		"onboarding_completed": user.OnboardingDone,
		"tenantIds":            user.TenantIDs,
		"tenant_ids":           user.TenantIDs,
		"defaultTenantId":      user.DefaultTenantID,
		"default_tenant_id":    user.DefaultTenantID,
		"shopIds":              user.ShopIDs,
		"shop_ids":             user.ShopIDs,
		"defaultShopId":        user.DefaultShopID,
		"default_shop_id":      user.DefaultShopID,
		"roles":                user.Roles,
		"permissions":          user.Permissions,
	}
}

func (h *AuthHandler) ensureTenantAdminPolicy(user auth.User) {
	if h.enforcer == nil {
		return
	}
	platformPolicies := [][2]string{
		{"/api/v1/auth/me", "GET"},
		{"/api/v1/auth/logout", "POST"},
		{"/api/v1/auth/shops", "POST"},
		{"/api/v1/account/profile", "PUT"},
		{"/api/v1/account/password", "PUT"},
		{"/api/v1/account/onboarding-completed", "POST"},
		{"/api/v1/workspace", "GET"},
		{"/api/v1/dashboard", "GET"},
		{"/api/v1/products", "GET"},
		{"/api/v1/products/analyze", "POST"},
		{"/api/v1/inventory/risks", "GET"},
		{"/api/v1/inventory/replenishment-plan", "POST"},
		{"/api/v1/campaigns", "GET"},
		{"/api/v1/campaigns/:campaign_id/review", "POST"},
		{"/api/v1/onboarding/complete", "POST"},
		{"/api/v1/ai-chat/messages", "POST"},
		{"/api/v1/reports", "(GET|POST)"},
		{"/api/v1/reports/*path", "(GET|POST|PUT|PATCH|DELETE)"},
		{"/api/v1/agents", "GET"},
		{"/api/v1/agents/*path", "(GET|POST|PUT|PATCH|DELETE)"},
		{"/api/v1/agents/strategies/:strategy_id/defer", "POST"},
		{"/api/v1/data-import/*path", "(GET|POST|PUT|PATCH|DELETE)"},
		{"/api/v1/shops", "(GET|POST)"},
		{"/api/v1/shops/*path", "(GET|POST|PUT|PATCH|DELETE)"},
		{"/api/v1/integrations", "GET"},
		{"/api/v1/integrations/*path", "(GET|POST|PUT|PATCH|DELETE)"},
		{"/api/v1/tasks", "POST"},
		{"/api/v1/tasks", "GET"},
		{"/api/v1/tasks/:thread_id", "GET"},
		{"/api/v1/tasks/:thread_id/cancel", "POST"},
		{"/api/v1/tasks/:thread_id/resume", "POST"},
		{"/api/v1/uploads", "POST"},
		{"/api/v1/files", "GET"},
		{"/api/v1/download", "GET"},
		{"/api/v1/tools/catalog", "GET"},
		{"/api/v1/memories/search", "POST"},
		{"/api/v1/memories/reviews", "GET"},
		{"/api/v1/memories/reviews/:review_id/approve", "POST"},
		{"/api/v1/memories/reviews/:review_id/reject", "POST"},
		{"/api/v1/policy/proposals", "GET"},
		{"/api/v1/policy/proposals/:proposal_id/approve", "POST"},
		{"/api/v1/policy/proposals/:proposal_id/reject", "POST"},
		{"/api/v1/traces/:task_id", "GET"},
		{"/api/v1/traces/:task_id/timeline", "GET"},
		{"/api/v1/metrics/agents", "GET"},
		{"/api/v1/ws/:thread_id", "GET"},
	}
	for _, tenantID := range user.TenantIDs {
		_, _ = h.enforcer.AddRoleForUserInDomain(user.ID, "admin", tenantID)
		// 本地开发注册出来的新租户复用 admin API 权限模板；生产环境应改为数据库策略和审批流。
		for _, policy := range platformPolicies {
			_, _ = h.enforcer.AddPolicy("admin", tenantID, policy[0], policy[1])
		}
	}
}

func registerErrorMessage(reason string) string {
	switch reason {
	case "user already exists":
		return "用户已存在，请直接登录"
	case "user id required":
		return "请输入用户名"
	case "password required":
		return "请输入密码"
	default:
		return "注册失败"
	}
}

func shopErrorMessage(reason string) string {
	switch reason {
	case "shop id required":
		return "请输入店铺标识"
	case "tenant forbidden":
		return "当前用户无权在该组织下创建店铺"
	default:
		return "店铺接入失败"
	}
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if value != "" {
			return value
		}
	}
	return ""
}
