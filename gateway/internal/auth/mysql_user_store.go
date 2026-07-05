//go:build mysql

package auth

import (
	"database/sql"
	"errors"
	"fmt"
	"os"
	"strings"
	"time"

	_ "github.com/go-sql-driver/mysql"
	"golang.org/x/crypto/bcrypt"
)

type MySQLUserStore struct {
	db *sql.DB
}

func newMySQLUserStoreFromEnv() (UserStore, error) {
	db, err := sql.Open("mysql", gatewayMySQLDSN())
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(10)
	db.SetMaxIdleConns(5)
	db.SetConnMaxLifetime(30 * time.Minute)
	store := &MySQLUserStore{db: db}
	if err := store.initSchema(); err != nil {
		_ = db.Close()
		return nil, err
	}
	if err := store.ensureDemoUser(); err != nil {
		_ = db.Close()
		return nil, err
	}
	return store, nil
}

func (s *MySQLUserStore) Authenticate(username string, password string) (User, error) {
	account := strings.TrimSpace(username)
	userID := normalizeIdentifier(account)
	if userID == "" && account == "" {
		return User{}, errors.New("invalid credentials")
	}
	var matchedUserID string
	var passwordHash string
	if err := s.db.QueryRow("SELECT id, password_hash FROM gateway_users WHERE (id = ? OR email = ?) AND status = 'active'", userID, account).Scan(&matchedUserID, &passwordHash); err != nil {
		return User{}, errors.New("invalid credentials")
	}
	if bcrypt.CompareHashAndPassword([]byte(passwordHash), []byte(password)) != nil {
		return User{}, errors.New("invalid credentials")
	}
	return s.loadUser(matchedUserID)
}

func (s *MySQLUserStore) FindByID(userID string) (User, bool) {
	user, err := s.loadUser(userID)
	return user, err == nil
}

func (s *MySQLUserStore) Register(input RegisterInput) (User, error) {
	userID := normalizeIdentifier(firstNonEmpty(input.UserID, input.Email))
	if userID == "" {
		return User{}, errors.New("user id required")
	}
	if strings.TrimSpace(input.Password) == "" {
		return User{}, errors.New("password required")
	}
	tenantID := normalizeIdentifier(firstNonEmpty(input.TenantID, input.TenantName, userID+"_org"))
	shopID := normalizeIdentifier(firstNonEmpty(input.ShopID, input.ShopName))
	passwordHash, err := bcrypt.GenerateFromPassword([]byte(input.Password), bcrypt.DefaultCost)
	if err != nil {
		return User{}, err
	}
	tx, err := s.db.Begin()
	if err != nil {
		return User{}, err
	}
	defer tx.Rollback()
	if _, err := tx.Exec("INSERT INTO gateway_users (id, name, email, phone, company_name, plan, password_hash, default_tenant_id, default_shop_id, onboarding_completed, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')", userID, firstNonEmpty(input.Name, userID), firstNonEmpty(input.Email, input.UserID), input.Phone, firstNonEmpty(input.CompanyName, input.TenantName, tenantID), firstNonEmpty(input.Plan, "团队版"), string(passwordHash), tenantID, nullIfEmpty(shopID), shopID != ""); err != nil {
		if strings.Contains(strings.ToLower(err.Error()), "duplicate") {
			return User{}, errors.New("user already exists")
		}
		return User{}, err
	}
	if err := upsertTenant(tx, tenantID, firstNonEmpty(input.TenantName, tenantID)); err != nil {
		return User{}, err
	}
	if _, err := tx.Exec("INSERT INTO gateway_user_tenants (user_id, tenant_id, role) VALUES (?, ?, 'admin')", userID, tenantID); err != nil {
		return User{}, err
	}
	if shopID != "" {
		if err := upsertShop(tx, tenantID, shopID, firstNonEmpty(input.ShopName, shopID), "connected"); err != nil {
			return User{}, err
		}
		if _, err := tx.Exec("INSERT INTO gateway_user_shops (user_id, tenant_id, shop_id) VALUES (?, ?, ?)", userID, tenantID, shopID); err != nil {
			return User{}, err
		}
	}
	if err := tx.Commit(); err != nil {
		return User{}, err
	}
	return s.loadUser(userID)
}

func (s *MySQLUserStore) AddShop(userID string, tenantID string, shopID string) (User, error) {
	shopID = normalizeIdentifier(shopID)
	if shopID == "" {
		return User{}, errors.New("shop id required")
	}
	user, err := s.loadUser(userID)
	if err != nil {
		return User{}, errors.New("user not found")
	}
	if tenantID == "" {
		tenantID = user.DefaultTenantID
	}
	if !contains(user.TenantIDs, tenantID) {
		return User{}, errors.New("tenant forbidden")
	}
	tx, err := s.db.Begin()
	if err != nil {
		return User{}, err
	}
	defer tx.Rollback()
	if err := upsertShop(tx, tenantID, shopID, shopID, "connected"); err != nil {
		return User{}, err
	}
	if _, err := tx.Exec("INSERT IGNORE INTO gateway_user_shops (user_id, tenant_id, shop_id) VALUES (?, ?, ?)", userID, tenantID, shopID); err != nil {
		return User{}, err
	}
	if user.DefaultShopID == "" {
		if _, err := tx.Exec("UPDATE gateway_users SET default_shop_id = ?, onboarding_completed = TRUE WHERE id = ?", shopID, userID); err != nil {
			return User{}, err
		}
	}
	if err := tx.Commit(); err != nil {
		return User{}, err
	}
	return s.loadUser(userID)
}

func (s *MySQLUserStore) initSchema() error {
	statements := []string{
		`CREATE TABLE IF NOT EXISTS gateway_tenants (id VARCHAR(64) PRIMARY KEY, name VARCHAR(128) NOT NULL, status VARCHAR(32) NOT NULL DEFAULT 'active', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
		`CREATE TABLE IF NOT EXISTS gateway_shops (id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, name VARCHAR(128) NOT NULL, auth_status VARCHAR(32) NOT NULL DEFAULT 'pending', data_status VARCHAR(32) NOT NULL DEFAULT 'empty', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, PRIMARY KEY (tenant_id, id), INDEX idx_gateway_shops_auth (tenant_id, auth_status), CONSTRAINT fk_gateway_shops_tenant FOREIGN KEY (tenant_id) REFERENCES gateway_tenants(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
		`CREATE TABLE IF NOT EXISTS gateway_users (id VARCHAR(64) PRIMARY KEY, name VARCHAR(128) NOT NULL, email VARCHAR(255) NULL, phone VARCHAR(64) NULL, company_name VARCHAR(128) NULL, plan VARCHAR(64) NOT NULL DEFAULT '团队版', password_hash VARCHAR(255) NOT NULL, default_tenant_id VARCHAR(64) NULL, default_shop_id VARCHAR(64) NULL, onboarding_completed BOOLEAN NOT NULL DEFAULT FALSE, status VARCHAR(32) NOT NULL DEFAULT 'active', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, UNIQUE KEY uk_gateway_users_email (email)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
		`CREATE TABLE IF NOT EXISTS gateway_user_tenants (user_id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, role VARCHAR(64) NOT NULL DEFAULT 'admin', PRIMARY KEY (user_id, tenant_id, role), INDEX idx_gateway_user_tenants_tenant (tenant_id), CONSTRAINT fk_gateway_user_tenants_user FOREIGN KEY (user_id) REFERENCES gateway_users(id), CONSTRAINT fk_gateway_user_tenants_tenant FOREIGN KEY (tenant_id) REFERENCES gateway_tenants(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
		`CREATE TABLE IF NOT EXISTS gateway_user_shops (user_id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, shop_id VARCHAR(64) NOT NULL, PRIMARY KEY (user_id, tenant_id, shop_id), CONSTRAINT fk_gateway_user_shops_user FOREIGN KEY (user_id) REFERENCES gateway_users(id), CONSTRAINT fk_gateway_user_shops_shop FOREIGN KEY (tenant_id, shop_id) REFERENCES gateway_shops(tenant_id, id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	}
	for _, statement := range statements {
		if _, err := s.db.Exec(statement); err != nil {
			return err
		}
	}
	for column, ddl := range map[string]string{
		"email":                "ALTER TABLE gateway_users ADD COLUMN email VARCHAR(255) NULL",
		"phone":                "ALTER TABLE gateway_users ADD COLUMN phone VARCHAR(64) NULL",
		"company_name":         "ALTER TABLE gateway_users ADD COLUMN company_name VARCHAR(128) NULL",
		"plan":                 "ALTER TABLE gateway_users ADD COLUMN plan VARCHAR(64) NOT NULL DEFAULT '团队版'",
		"onboarding_completed": "ALTER TABLE gateway_users ADD COLUMN onboarding_completed BOOLEAN NOT NULL DEFAULT FALSE",
	} {
		if exists, err := s.columnExists("gateway_users", column); err != nil {
			return err
		} else if !exists {
			if _, err := s.db.Exec(ddl); err != nil {
				return err
			}
		}
	}
	return nil
}

func (s *MySQLUserStore) ensureDemoUser() error {
	userID := envOrDefault("GATEWAY_DEMO_USER_ID", "local_user")
	if _, found := s.FindByID(userID); found {
		return nil
	}
	_, err := s.Register(RegisterInput{
		UserID:      userID,
		Name:        envOrDefault("GATEWAY_DEMO_USER_NAME", "本地管理员"),
		Email:       envOrDefault("GATEWAY_DEMO_USER_EMAIL", "operator@example.com"),
		CompanyName: envOrDefault("GATEWAY_DEMO_COMPANY_NAME", "EcomPilot 示例公司"),
		Password:    envOrDefault("GATEWAY_DEMO_PASSWORD", "admin123"),
		TenantID:    envOrDefault("GATEWAY_DEMO_TENANT_ID", "tenant_demo"),
		TenantName:  envOrDefault("GATEWAY_DEMO_TENANT_ID", "tenant_demo"),
		ShopID:      envOrDefault("GATEWAY_DEMO_SHOP_ID", "default_shop"),
		ShopName:    envOrDefault("GATEWAY_DEMO_SHOP_ID", "default_shop"),
	})
	return err
}

func (s *MySQLUserStore) loadUser(userID string) (User, error) {
	var user User
	var defaultTenant, defaultShop, email, phone, companyName, plan sql.NullString
	var createdAt time.Time
	if err := s.db.QueryRow("SELECT id, name, email, phone, company_name, plan, default_tenant_id, default_shop_id, onboarding_completed, created_at FROM gateway_users WHERE id = ? AND status = 'active'", userID).Scan(&user.ID, &user.Name, &email, &phone, &companyName, &plan, &defaultTenant, &defaultShop, &user.OnboardingDone, &createdAt); err != nil {
		return User{}, err
	}
	user.Email = email.String
	user.Phone = phone.String
	user.CompanyName = companyName.String
	user.Plan = firstNonEmpty(plan.String, "团队版")
	user.CreatedAt = createdAt.Format(time.RFC3339)
	user.DefaultTenantID = defaultTenant.String
	user.DefaultShopID = defaultShop.String
	user.Permissions = splitCSV(envOrDefault("GATEWAY_DEMO_PERMISSIONS", "task:create,task:read,task:cancel,task:resume,file:upload,file:download,file:list,memory:read,memory:review,memory:approve,memory:reject,policy:read,policy:approve,policy:reject,trace:read,metrics:read,tool:database:read,tool:network:search,tool:kb:ask"))
	rows, err := s.db.Query("SELECT tenant_id, role FROM gateway_user_tenants WHERE user_id = ?", userID)
	if err != nil {
		return User{}, err
	}
	defer rows.Close()
	roleSet := map[string]bool{}
	for rows.Next() {
		var tenantID, role string
		if err := rows.Scan(&tenantID, &role); err != nil {
			return User{}, err
		}
		if !contains(user.TenantIDs, tenantID) {
			user.TenantIDs = append(user.TenantIDs, tenantID)
		}
		if !roleSet[role] {
			user.Roles = append(user.Roles, role)
			roleSet[role] = true
		}
	}
	shopRows, err := s.db.Query("SELECT shop_id FROM gateway_user_shops WHERE user_id = ? ORDER BY shop_id", userID)
	if err != nil {
		return User{}, err
	}
	defer shopRows.Close()
	for shopRows.Next() {
		var shopID string
		if err := shopRows.Scan(&shopID); err != nil {
			return User{}, err
		}
		if !contains(user.ShopIDs, shopID) {
			user.ShopIDs = append(user.ShopIDs, shopID)
		}
	}
	return user, nil
}

func (s *MySQLUserStore) UpdateProfile(userID string, input ProfileInput) (User, error) {
	if _, err := s.db.Exec("UPDATE gateway_users SET name=COALESCE(NULLIF(?, ''), name), email=COALESCE(NULLIF(?, ''), email), phone=COALESCE(NULLIF(?, ''), phone), company_name=COALESCE(NULLIF(?, ''), company_name) WHERE id=?", input.Name, input.Email, input.Phone, input.CompanyName, userID); err != nil {
		return User{}, err
	}
	return s.loadUser(userID)
}

func (s *MySQLUserStore) UpdatePassword(userID string, currentPassword string, newPassword string) error {
	if strings.TrimSpace(newPassword) == "" {
		return errors.New("password required")
	}
	var passwordHash string
	if err := s.db.QueryRow("SELECT password_hash FROM gateway_users WHERE id=? AND status='active'", userID).Scan(&passwordHash); err != nil {
		return errors.New("invalid credentials")
	}
	if bcrypt.CompareHashAndPassword([]byte(passwordHash), []byte(currentPassword)) != nil {
		return errors.New("invalid credentials")
	}
	nextHash, err := bcrypt.GenerateFromPassword([]byte(newPassword), bcrypt.DefaultCost)
	if err != nil {
		return err
	}
	_, err = s.db.Exec("UPDATE gateway_users SET password_hash=? WHERE id=?", string(nextHash), userID)
	return err
}

func (s *MySQLUserStore) SetOnboardingCompleted(userID string, completed bool) (User, error) {
	if _, err := s.db.Exec("UPDATE gateway_users SET onboarding_completed=? WHERE id=?", completed, userID); err != nil {
		return User{}, err
	}
	return s.loadUser(userID)
}

func (s *MySQLUserStore) columnExists(tableName string, columnName string) (bool, error) {
	var count int
	if err := s.db.QueryRow("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = ? AND column_name = ?", tableName, columnName).Scan(&count); err != nil {
		return false, err
	}
	return count > 0, nil
}

func upsertTenant(tx *sql.Tx, tenantID string, name string) error {
	_, err := tx.Exec("INSERT INTO gateway_tenants (id, name, status) VALUES (?, ?, 'active') ON DUPLICATE KEY UPDATE name = VALUES(name), status = 'active'", tenantID, name)
	return err
}

func upsertShop(tx *sql.Tx, tenantID string, shopID string, name string, authStatus string) error {
	_, err := tx.Exec("INSERT INTO gateway_shops (tenant_id, id, name, auth_status, data_status) VALUES (?, ?, ?, ?, 'empty') ON DUPLICATE KEY UPDATE name = VALUES(name), auth_status = VALUES(auth_status)", tenantID, shopID, name, authStatus)
	return err
}

func nullIfEmpty(value string) interface{} {
	if value == "" {
		return nil
	}
	return value
}

func gatewayMySQLDSN() string {
	user := os.Getenv("GATEWAY_MYSQL_USER")
	if user == "" {
		user = os.Getenv("MYSQL_USER")
	}
	password := os.Getenv("GATEWAY_MYSQL_PASSWORD")
	if password == "" {
		password = os.Getenv("MYSQL_PASSWORD")
	}
	host := envOrDefault("GATEWAY_MYSQL_HOST", envOrDefault("MYSQL_HOST", "localhost"))
	port := envOrDefault("GATEWAY_MYSQL_PORT", envOrDefault("MYSQL_PORT", "3306"))
	database := envOrDefault("GATEWAY_MYSQL_DATABASE", envOrDefault("MYSQL_DATABASE", "ecommerce_demo"))
	return fmt.Sprintf("%s:%s@tcp(%s:%s)/%s?parseTime=true&charset=utf8mb4", user, password, host, port, database)
}
