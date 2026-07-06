package auth

import (
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
)

// User 是网关本地开发阶段持有的用户身份记录。
// 网关是整个平台的身份信任边界：Python Brain 只接收网关注入的用户、租户、店铺上下文，
// 不应该再相信浏览器 body 里自带的 tenant_id/user_id/shop_id。
type User struct {
	ID              string   `json:"id"`
	Name            string   `json:"name"`
	Email           string   `json:"email"`
	Phone           string   `json:"phone"`
	CompanyName     string   `json:"company_name"`
	Plan            string   `json:"plan"`
	CreatedAt       string   `json:"created_at"`
	OnboardingDone  bool     `json:"onboarding_completed"`
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
	Register(input RegisterInput) (User, error)
	AddShop(userID string, tenantID string, shopID string) (User, error)
	UpdateProfile(userID string, input ProfileInput) (User, error)
	UpdatePassword(userID string, currentPassword string, newPassword string) error
	SetOnboardingCompleted(userID string, completed bool) (User, error)
	Backend() StoreBackendInfo
}

type StoreBackendInfo struct {
	Backend       string
	MySQLDatabase string
}

type RegisterInput struct {
	UserID      string
	Name        string
	Email       string
	Phone       string
	CompanyName string
	Plan        string
	Password    string
	TenantID    string
	TenantName  string
	ShopID      string
	ShopName    string
}

type ProfileInput struct {
	Name        string
	Email       string
	Phone       string
	CompanyName string
}

type StaticUserStore struct {
	mu       sync.RWMutex
	users    map[string]User
	filePath string
}

func NewUserStoreFromConfig(backend string, mode string) (UserStore, error) {
	selectedBackend := strings.ToLower(strings.TrimSpace(backend))
	if selectedBackend == "" {
		selectedBackend = "mysql"
	}
	if selectedBackend == "mysql" {
		return newMySQLUserStoreFromEnv()
	}
	if selectedBackend == "memory" {
		if !strings.EqualFold(mode, "debug") && !strings.EqualFold(mode, "test") && os.Getenv("GO_WANT_HELPER_PROCESS") == "" {
			return nil, fmt.Errorf("%s user store is dev/test only; set GATEWAY_USER_STORE_BACKEND=mysql for commercial mode", selectedBackend)
		}
		return NewStaticUserStore(), nil
	}
	if selectedBackend == "static" {
		return nil, fmt.Errorf("static/json user store is no longer a supported runtime backend; use mysql, or memory in debug/test only")
	}
	return nil, fmt.Errorf("unsupported user store backend: %s", backend)
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
		Name:            envOrDefault("GATEWAY_DEMO_USER_NAME", "Demo Admin"),
		Email:           envOrDefault("GATEWAY_DEMO_USER_EMAIL", "operator@example.com"),
		CompanyName:     envOrDefault("GATEWAY_DEMO_COMPANY_NAME", "EcomPilot Demo Company"),
		Plan:            envOrDefault("GATEWAY_DEMO_PLAN", "Team"),
		CreatedAt:       "local_dev",
		OnboardingDone:  true,
		Password:        password,
		TenantIDs:       splitCSV(envOrDefault("GATEWAY_DEMO_TENANT_IDS", defaultTenant)),
		DefaultTenantID: defaultTenant,
		ShopIDs:         splitCSV(envOrDefault("GATEWAY_DEMO_SHOP_IDS", defaultShop)),
		DefaultShopID:   defaultShop,
		Roles:           roles,
		Permissions:     permissions,
	}

	// memory/static is dev/test only. JSON is used only when GATEWAY_STATIC_USER_FILE is explicit.
	store := &StaticUserStore{users: map[string]User{userID: user}, filePath: os.Getenv("GATEWAY_STATIC_USER_FILE")}
	store.loadFromFile()
	if _, ok := store.users[userID]; !ok {
		store.users[userID] = user
		store.persistLocked()
	}
	return store
}

func (s *StaticUserStore) Backend() StoreBackendInfo {
	return StoreBackendInfo{Backend: "memory"}
}

func (s *StaticUserStore) Authenticate(username string, password string) (User, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	user, ok := s.users[username]
	if !ok {
		for _, candidate := range s.users {
			if strings.EqualFold(candidate.Email, strings.TrimSpace(username)) {
				user = candidate
				ok = true
				break
			}
		}
	}
	if !ok {
		return User{}, errors.New("invalid credentials")
	}
	if subtle.ConstantTimeCompare([]byte(user.Password), []byte(password)) != 1 {
		return User{}, errors.New("invalid credentials")
	}
	return user, nil
}

func (s *StaticUserStore) FindByID(userID string) (User, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	user, ok := s.users[userID]
	return user, ok
}

func (s *StaticUserStore) Register(input RegisterInput) (User, error) {
	userID := normalizeIdentifier(firstNonEmpty(input.UserID, input.Email, input.Phone))
	if userID == "" {
		return User{}, errors.New("user id required")
	}
	if strings.TrimSpace(input.Password) == "" {
		return User{}, errors.New("password required")
	}
	tenantID := normalizeIdentifier(input.TenantID)
	if tenantID == "" {
		tenantID = normalizeIdentifier(input.TenantName)
	}
	if tenantID == "" {
		tenantID = userID + "_org"
	}
	shopID := normalizeIdentifier(input.ShopID)
	if shopID == "" {
		shopID = normalizeIdentifier(input.ShopName)
	}

	s.mu.Lock()
	defer s.mu.Unlock()
	if _, exists := s.users[userID]; exists {
		return User{}, errors.New("user already exists")
	}
	user := User{
		ID:              userID,
		Name:            firstNonEmpty(input.Name, userID),
		Email:           firstNonEmpty(input.Email, input.UserID),
		Phone:           input.Phone,
		CompanyName:     firstNonEmpty(input.CompanyName, input.TenantName, tenantID),
		Plan:            firstNonEmpty(input.Plan, "Team"),
		CreatedAt:       "now",
		OnboardingDone:  shopID != "",
		Password:        input.Password,
		TenantIDs:       []string{tenantID},
		DefaultTenantID: tenantID,
		ShopIDs:         []string{},
		DefaultShopID:   "",
		Roles:           []string{"admin"},
		Permissions:     splitCSV(envOrDefault("GATEWAY_DEMO_PERMISSIONS", "task:create,task:read,task:cancel,task:resume,file:upload,file:download,file:list,memory:read,memory:review,memory:approve,memory:reject,policy:read,policy:approve,policy:reject,trace:read,metrics:read,tool:database:read,tool:network:search,tool:kb:ask")),
	}
	if shopID != "" {
		user.ShopIDs = []string{shopID}
		user.DefaultShopID = shopID
	}
	s.users[userID] = user
	s.persistLocked()
	return user, nil
}

func (s *StaticUserStore) AddShop(userID string, tenantID string, shopID string) (User, error) {
	shopID = normalizeIdentifier(shopID)
	if shopID == "" {
		return User{}, errors.New("shop id required")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	user, ok := s.users[userID]
	if !ok {
		return User{}, errors.New("user not found")
	}
	if tenantID != "" && !contains(user.TenantIDs, tenantID) {
		return User{}, errors.New("tenant forbidden")
	}
	if !contains(user.ShopIDs, shopID) {
		user.ShopIDs = append(user.ShopIDs, shopID)
	}
	if user.DefaultShopID == "" {
		user.DefaultShopID = shopID
	}
	user.OnboardingDone = true
	s.users[userID] = user
	s.persistLocked()
	return user, nil
}

func (s *StaticUserStore) UpdateProfile(userID string, input ProfileInput) (User, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	user, ok := s.users[userID]
	if !ok {
		return User{}, errors.New("user not found")
	}
	user.Name = firstNonEmpty(input.Name, user.Name)
	user.Email = firstNonEmpty(input.Email, user.Email)
	user.Phone = firstNonEmpty(input.Phone, user.Phone)
	user.CompanyName = firstNonEmpty(input.CompanyName, user.CompanyName)
	s.users[userID] = user
	s.persistLocked()
	return user, nil
}

func (s *StaticUserStore) UpdatePassword(userID string, currentPassword string, newPassword string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	user, ok := s.users[userID]
	if !ok {
		return errors.New("user not found")
	}
	if subtle.ConstantTimeCompare([]byte(user.Password), []byte(currentPassword)) != 1 {
		return errors.New("invalid credentials")
	}
	if strings.TrimSpace(newPassword) == "" {
		return errors.New("password required")
	}
	user.Password = newPassword
	s.users[userID] = user
	s.persistLocked()
	return nil
}

func (s *StaticUserStore) SetOnboardingCompleted(userID string, completed bool) (User, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	user, ok := s.users[userID]
	if !ok {
		return User{}, errors.New("user not found")
	}
	user.OnboardingDone = completed
	s.users[userID] = user
	s.persistLocked()
	return user, nil
}

func (s *StaticUserStore) loadFromFile() {
	if s.filePath == "" {
		return
	}
	data, err := os.ReadFile(s.filePath)
	if err != nil {
		return
	}
	var users map[string]User
	if json.Unmarshal(data, &users) != nil {
		return
	}
	for id, user := range users {
		if strings.TrimSpace(id) != "" {
			s.users[id] = user
		}
	}
}

func (s *StaticUserStore) persistLocked() {
	if s.filePath == "" {
		return
	}
	if err := os.MkdirAll(filepath.Dir(s.filePath), 0o755); err != nil {
		return
	}
	data, err := json.MarshalIndent(s.users, "", "  ")
	if err != nil {
		return
	}
	_ = os.WriteFile(s.filePath, data, 0o600)
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

func normalizeIdentifier(value string) string {
	trimmed := strings.TrimSpace(strings.ToLower(value))
	if trimmed == "" {
		return ""
	}
	replacer := regexp.MustCompile(`[^a-z0-9_\-]+`)
	normalized := replacer.ReplaceAllString(trimmed, "_")
	return strings.Trim(normalized, "_-")
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		trimmed := strings.TrimSpace(value)
		if trimmed != "" {
			return trimmed
		}
	}
	return ""
}

func contains(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}
