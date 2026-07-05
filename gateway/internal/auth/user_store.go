package auth

import (
	"crypto/subtle"
	"errors"
	"os"
	"strings"
)

// User 是网关本地开发阶段持有的用户身份记录。
// 网关是整个平台的身份信任边界：Python Brain 只接收网关注入的用户、租户、店铺上下文，
// 不应该再相信浏览器 body 里自带的 tenant_id/user_id/shop_id。
type User struct {
	ID              string   `json:"id"`
	Name            string   `json:"name"`
	Password        string   `json:"-"`
	TenantIDs       []string `json:"tenant_ids"`
	DefaultTenantID string   `json:"default_tenant_id"`
	ShopIDs         []string `json:"shop_ids"`
	DefaultShopID   string   `json:"default_shop_id"`
	Roles           []string `json:"roles"`
	Permissions     []string `json:"permissions"`
}

// UserStore 抽象用户来源。
// 当前实现是本地静态用户，后续接 SSO、OAuth2、企业 IAM 或数据库用户表时，
// 只需要替换这个接口实现，不需要改 Auth/Tenant/Casbin 中间件。
type UserStore interface {
	Authenticate(username string, password string) (User, error)
	FindByID(userID string) (User, bool)
}

type StaticUserStore struct {
	users map[string]User
}

func NewStaticUserStore() *StaticUserStore {
	// 默认值和 gateway/configs/casbin/policy.csv 中的本地策略保持一致，
	// 确保本地登录后能通过 tenant_demo 下的 admin 角色访问 API。
	userID := envOrDefault("GATEWAY_DEMO_USER_ID", "local_user")
	password := envOrDefault("GATEWAY_DEMO_PASSWORD", "admin123")
	defaultTenant := envOrDefault("GATEWAY_DEMO_TENANT_ID", "tenant_demo")
	defaultShop := envOrDefault("GATEWAY_DEMO_SHOP_ID", "default_shop")
	roles := splitCSV(envOrDefault("GATEWAY_DEMO_ROLES", "admin"))
	permissions := splitCSV(envOrDefault("GATEWAY_DEMO_PERMISSIONS", "task:create,task:read,task:cancel,task:resume,file:upload,file:download,file:list,memory:read,memory:review,memory:approve,memory:reject,policy:read,policy:approve,policy:reject,trace:read,metrics:read,tool:database:read,tool:network:search,tool:kb:ask"))

	user := User{
		ID:              userID,
		Name:            envOrDefault("GATEWAY_DEMO_USER_NAME", "本地管理员"),
		Password:        password,
		TenantIDs:       splitCSV(envOrDefault("GATEWAY_DEMO_TENANT_IDS", defaultTenant)),
		DefaultTenantID: defaultTenant,
		ShopIDs:         splitCSV(envOrDefault("GATEWAY_DEMO_SHOP_IDS", defaultShop)),
		DefaultShopID:   defaultShop,
		Roles:           roles,
		Permissions:     permissions,
	}

	return &StaticUserStore{users: map[string]User{userID: user}}
}

func (s *StaticUserStore) Authenticate(username string, password string) (User, error) {
	user, ok := s.users[username]
	if !ok {
		return User{}, errors.New("invalid credentials")
	}
	if subtle.ConstantTimeCompare([]byte(user.Password), []byte(password)) != 1 {
		return User{}, errors.New("invalid credentials")
	}
	return user, nil
}

func (s *StaticUserStore) FindByID(userID string) (User, bool) {
	user, ok := s.users[userID]
	return user, ok
}

func splitCSV(value string) []string {
	parts := strings.Split(value, ",")
	items := make([]string, 0, len(parts))
	for _, part := range parts {
		trimmed := strings.TrimSpace(part)
		if trimmed != "" {
			items = append(items, trimmed)
		}
	}
	return items
}

func envOrDefault(key string, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}
