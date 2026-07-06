package auth

import (
	"database/sql"
	"errors"
	"fmt"
	"os"
	"strings"
	"time"
	"unicode/utf8"

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
	if err := db.Ping(); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("connect gateway mysql user store failed: %w", err)
	}
	store := &MySQLUserStore{db: db}
	if err := store.initSchema(); err != nil {
		_ = db.Close()
		return nil, err
	}
	if err := store.repairMojibakeSeedData(); err != nil {
		_ = db.Close()
		return nil, err
	}
	if err := store.ensureDemoUser(); err != nil {
		_ = db.Close()
		return nil, err
	}
	return store, nil
}

func (s *MySQLUserStore) Backend() StoreBackendInfo {
	return StoreBackendInfo{Backend: "mysql", MySQLDatabase: gatewayMySQLDatabase()}
}

func (s *MySQLUserStore) Authenticate(username string, password string) (User, error) {
	account := strings.TrimSpace(username)
	userID := normalizeIdentifier(account)
	if userID == "" && account == "" {
		return User{}, errors.New("invalid credentials")
	}
	var matchedUserID string
	var passwordHash string
	if err := s.db.QueryRow("SELECT id, password_hash FROM gateway_users WHERE (id = ? OR email = ? OR phone = ?) AND status = 'active'", userID, account, account).Scan(&matchedUserID, &passwordHash); err != nil {
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
	account := firstNonEmpty(input.UserID, input.Email, input.Phone)
	userID := normalizeIdentifier(account)
	if userID == "" {
		return User{}, errors.New("account required")
	}
	if strings.TrimSpace(input.Password) == "" {
		return User{}, errors.New("password required")
	}
	tenantID := normalizeIdentifier(input.TenantID)
	if tenantID == "" {
		tenantID = userID + "_org"
	}
	shopID := normalizeIdentifier(input.ShopID)
	passwordHash, err := bcrypt.GenerateFromPassword([]byte(input.Password), bcrypt.DefaultCost)
	if err != nil {
		return User{}, err
	}
	tx, err := s.db.Begin()
	if err != nil {
		return User{}, err
	}
	defer tx.Rollback()
	if err := upsertTenant(tx, tenantID, cleanDisplayText(firstNonEmpty(input.TenantName, input.CompanyName, tenantID))); err != nil {
		return User{}, err
	}
	if _, err := tx.Exec("INSERT INTO gateway_users (id, name, email, phone, company_name, plan, password_hash, default_tenant_id, default_shop_id, onboarding_completed, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')", userID, cleanDisplayText(firstNonEmpty(input.Name, userID)), nullIfEmpty(input.Email), nullIfEmpty(input.Phone), cleanDisplayText(firstNonEmpty(input.CompanyName, input.TenantName, tenantID)), cleanDisplayText(firstNonEmpty(input.Plan, "Team")), string(passwordHash), tenantID, nullIfEmpty(shopID), shopID != ""); err != nil {
		if strings.Contains(strings.ToLower(err.Error()), "duplicate") {
			return User{}, errors.New("user already exists")
		}
		return User{}, err
	}
	if _, err := tx.Exec("INSERT INTO gateway_user_tenants (user_id, tenant_id, role) VALUES (?, ?, 'admin')", userID, tenantID); err != nil {
		return User{}, err
	}
	if shopID != "" {
		if err := upsertShop(tx, tenantID, shopID, cleanDisplayText(firstNonEmpty(input.ShopName, shopID)), "connected"); err != nil {
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
	tenantID = normalizeIdentifier(tenantID)
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
	if _, err := tx.Exec("UPDATE gateway_users SET default_shop_id = ?, onboarding_completed = TRUE WHERE id = ?", shopID, userID); err != nil {
		return User{}, err
	}
	if err := tx.Commit(); err != nil {
		return User{}, err
	}
	return s.loadUser(userID)
}

func (s *MySQLUserStore) initSchema() error {
	statements := []string{
		`CREATE TABLE IF NOT EXISTS gateway_tenants (id VARCHAR(64) PRIMARY KEY, name VARCHAR(128) NOT NULL, status VARCHAR(32) NOT NULL DEFAULT 'active', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
		`CREATE TABLE IF NOT EXISTS gateway_shops (tenant_id VARCHAR(64) NOT NULL, id VARCHAR(64) NOT NULL, name VARCHAR(128) NOT NULL, category VARCHAR(128) NULL, platform VARCHAR(128) NULL, shop_type VARCHAR(64) NULL, business_stage VARCHAR(64) NULL, status VARCHAR(32) NOT NULL DEFAULT 'active', auth_status VARCHAR(32) NOT NULL DEFAULT 'pending', data_status VARCHAR(32) NOT NULL DEFAULT 'empty', last_sync_at DATETIME NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, PRIMARY KEY (tenant_id, id), INDEX idx_gateway_shops_auth (tenant_id, auth_status), CONSTRAINT fk_gateway_shops_tenant FOREIGN KEY (tenant_id) REFERENCES gateway_tenants(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
		`CREATE TABLE IF NOT EXISTS gateway_users (id VARCHAR(64) PRIMARY KEY, name VARCHAR(128) NOT NULL, email VARCHAR(255) NULL, phone VARCHAR(64) NULL, company_name VARCHAR(128) NULL, plan VARCHAR(64) NOT NULL DEFAULT 'Team', password_hash VARCHAR(255) NOT NULL, default_tenant_id VARCHAR(64) NULL, default_shop_id VARCHAR(64) NULL, onboarding_completed BOOLEAN NOT NULL DEFAULT FALSE, status VARCHAR(32) NOT NULL DEFAULT 'active', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, UNIQUE KEY uk_gateway_users_email (email), UNIQUE KEY uk_gateway_users_phone (phone)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
		`CREATE TABLE IF NOT EXISTS gateway_user_tenants (user_id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, role VARCHAR(64) NOT NULL DEFAULT 'admin', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, tenant_id, role), INDEX idx_gateway_user_tenants_tenant (tenant_id), CONSTRAINT fk_gateway_user_tenants_user FOREIGN KEY (user_id) REFERENCES gateway_users(id), CONSTRAINT fk_gateway_user_tenants_tenant FOREIGN KEY (tenant_id) REFERENCES gateway_tenants(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
		`CREATE TABLE IF NOT EXISTS gateway_user_shops (user_id VARCHAR(64) NOT NULL, tenant_id VARCHAR(64) NOT NULL, shop_id VARCHAR(64) NOT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, tenant_id, shop_id), CONSTRAINT fk_gateway_user_shops_user FOREIGN KEY (user_id) REFERENCES gateway_users(id), CONSTRAINT fk_gateway_user_shops_shop FOREIGN KEY (tenant_id, shop_id) REFERENCES gateway_shops(tenant_id, id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
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
		"plan":                 "ALTER TABLE gateway_users ADD COLUMN plan VARCHAR(64) NOT NULL DEFAULT 'Team'",
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
	for indexName, ddl := range map[string]string{
		"uk_gateway_users_email": "ALTER TABLE gateway_users ADD UNIQUE KEY uk_gateway_users_email (email)",
		"uk_gateway_users_phone": "ALTER TABLE gateway_users ADD UNIQUE KEY uk_gateway_users_phone (phone)",
	} {
		if exists, err := s.indexExists("gateway_users", indexName); err != nil {
			return err
		} else if !exists {
			if _, err := s.db.Exec(ddl); err != nil {
				return err
			}
		}
	}
	for column, ddl := range map[string]string{
		"category":       "ALTER TABLE gateway_shops ADD COLUMN category VARCHAR(128) NULL",
		"platform":       "ALTER TABLE gateway_shops ADD COLUMN platform VARCHAR(128) NULL",
		"shop_type":      "ALTER TABLE gateway_shops ADD COLUMN shop_type VARCHAR(64) NULL",
		"business_stage": "ALTER TABLE gateway_shops ADD COLUMN business_stage VARCHAR(64) NULL",
		"status":         "ALTER TABLE gateway_shops ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'active'",
		"last_sync_at":   "ALTER TABLE gateway_shops ADD COLUMN last_sync_at DATETIME NULL",
	} {
		if exists, err := s.columnExists("gateway_shops", column); err != nil {
			return err
		} else if !exists {
			if _, err := s.db.Exec(ddl); err != nil {
				return err
			}
		}
	}
	for column, ddl := range map[string]string{
		"created_at": "ALTER TABLE gateway_user_tenants ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP",
	} {
		if exists, err := s.columnExists("gateway_user_tenants", column); err != nil {
			return err
		} else if !exists {
			if _, err := s.db.Exec(ddl); err != nil {
				return err
			}
		}
	}
	for column, ddl := range map[string]string{
		"created_at": "ALTER TABLE gateway_user_shops ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP",
	} {
		if exists, err := s.columnExists("gateway_user_shops", column); err != nil {
			return err
		} else if !exists {
			if _, err := s.db.Exec(ddl); err != nil {
				return err
			}
		}
	}
	return nil
}

func (s *MySQLUserStore) repairMojibakeSeedData() error {
	statements := []string{
		"UPDATE gateway_users SET name = 'Demo Admin' WHERE email = 'operator@example.com' AND HEX(name) IN ('E98F88EE8480E6B9B4E7BBA0EFBC84E6828AE98D9B3F')",
		"UPDATE gateway_users SET company_name = 'EcomPilot Demo Company' WHERE email = 'operator@example.com' AND (company_name LIKE 'EcomPilot %' OR company_name IS NULL OR company_name = '')",
		"UPDATE gateway_users SET plan = 'Team' WHERE email = 'operator@example.com' AND plan <> 'Team'",
		"UPDATE gateway_shops SET category = 'apparel' WHERE HEX(category) = 'E69C8DE9A5B0E99E8BE58C85'",
		"UPDATE gateway_shops SET platform = 'taobao_tmall' WHERE HEX(platform) = 'E6B798E5AE9D202F20E5A4A9E78CAB'",
		"UPDATE gateway_shops SET shop_type = 'brand_owned' WHERE HEX(shop_type) = 'E59381E7898CE887AAE890A5'",
		"UPDATE gateway_shops SET business_stage = 'growth' WHERE HEX(business_stage) = 'E68890E995BFE69C9F'",
		"UPDATE gateway_shops SET category = 'unset' WHERE category IS NULL OR category = '' OR category = '????'",
		"UPDATE gateway_shops SET platform = 'taobao_tmall' WHERE platform IS NULL OR platform = '' OR platform = '?? / ??'",
		"UPDATE gateway_shops SET shop_type = 'brand_owned' WHERE shop_type IS NULL OR shop_type = '' OR shop_type = '????'",
		"UPDATE gateway_shops SET business_stage = 'growth' WHERE business_stage IS NULL OR business_stage = '' OR business_stage = '???'",
	}
	for _, statement := range statements {
		if _, err := s.db.Exec(statement); err != nil {
			return err
		}
	}
	return s.migrateTenantIDByHex("6C626BC3A5C2B0C28FC3A5C2BAC297", "lbk_shop", "lbk_shop")
}

func (s *MySQLUserStore) migrateTenantIDByHex(oldHex string, newTenantID string, newTenantName string) error {
	var count int
	if err := s.db.QueryRow("SELECT COUNT(*) FROM gateway_tenants WHERE HEX(id) = ?", oldHex).Scan(&count); err != nil {
		return err
	}
	if count == 0 {
		return nil
	}
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := tx.Exec("SET FOREIGN_KEY_CHECKS=0"); err != nil {
		return err
	}
	statements := []string{
		"INSERT INTO gateway_tenants (id, name, status) VALUES (?, ?, 'active') ON DUPLICATE KEY UPDATE name = VALUES(name), status = 'active'",
		"UPDATE gateway_users SET default_tenant_id = ? WHERE HEX(default_tenant_id) = ?",
		"UPDATE gateway_user_tenants SET tenant_id = ? WHERE HEX(tenant_id) = ?",
		"UPDATE gateway_user_shops SET tenant_id = ? WHERE HEX(tenant_id) = ?",
		"UPDATE gateway_shops SET tenant_id = ? WHERE HEX(tenant_id) = ?",
		"DELETE FROM gateway_tenants WHERE HEX(id) = ?",
	}
	args := [][]interface{}{
		{newTenantID, newTenantName},
		{newTenantID, oldHex},
		{newTenantID, oldHex},
		{newTenantID, oldHex},
		{newTenantID, oldHex},
		{oldHex},
	}
	for i, statement := range statements {
		if _, err := tx.Exec(statement, args[i]...); err != nil {
			_, _ = tx.Exec("SET FOREIGN_KEY_CHECKS=1")
			return err
		}
	}
	if _, err := tx.Exec("SET FOREIGN_KEY_CHECKS=1"); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *MySQLUserStore) ensureDemoUser() error {
	userID := envOrDefault("GATEWAY_DEMO_USER_ID", "local_user")
	email := envOrDefault("GATEWAY_DEMO_USER_EMAIL", "operator@example.com")
	phone := os.Getenv("GATEWAY_DEMO_USER_PHONE")
	tenantID := envOrDefault("GATEWAY_DEMO_TENANT_ID", "tenant_demo")
	shopID := envOrDefault("GATEWAY_DEMO_SHOP_ID", "default_shop")
	if existingUserID, found := s.findUserIDByAccount(userID, email, phone); found {
		return s.ensureUserBindings(existingUserID, tenantID, envOrDefault("GATEWAY_DEMO_TENANT_ID", "tenant_demo"), shopID, envOrDefault("GATEWAY_DEMO_SHOP_ID", "default_shop"))
	}
	_, err := s.Register(RegisterInput{
		UserID:      userID,
		Name:        envOrDefault("GATEWAY_DEMO_USER_NAME", "Demo Admin"),
		Email:       email,
		Phone:       phone,
		CompanyName: envOrDefault("GATEWAY_DEMO_COMPANY_NAME", "EcomPilot Demo Company"),
		Password:    envOrDefault("GATEWAY_DEMO_PASSWORD", "admin123"),
		TenantID:    tenantID,
		TenantName:  tenantID,
		ShopID:      shopID,
		ShopName:    shopID,
	})
	return err
}

func (s *MySQLUserStore) findUserIDByAccount(values ...string) (string, bool) {
	for _, value := range values {
		account := strings.TrimSpace(value)
		if account == "" {
			continue
		}
		var userID string
		if err := s.db.QueryRow("SELECT id FROM gateway_users WHERE (id = ? OR email = ? OR phone = ?) AND status = 'active' LIMIT 1", normalizeIdentifier(account), account, account).Scan(&userID); err == nil {
			return userID, true
		}
	}
	return "", false
}

func (s *MySQLUserStore) ensureUserBindings(userID string, tenantID string, tenantName string, shopID string, shopName string) error {
	tenantID = normalizeIdentifier(tenantID)
	shopID = normalizeIdentifier(shopID)
	if tenantID == "" {
		return nil
	}
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err := upsertTenant(tx, tenantID, firstNonEmpty(tenantName, tenantID)); err != nil {
		return err
	}
	if _, err := tx.Exec("INSERT IGNORE INTO gateway_user_tenants (user_id, tenant_id, role) VALUES (?, ?, 'admin')", userID, tenantID); err != nil {
		return err
	}
	if shopID != "" {
		if err := upsertShop(tx, tenantID, shopID, firstNonEmpty(shopName, shopID), "connected"); err != nil {
			return err
		}
		if _, err := tx.Exec("INSERT IGNORE INTO gateway_user_shops (user_id, tenant_id, shop_id) VALUES (?, ?, ?)", userID, tenantID, shopID); err != nil {
			return err
		}
		if _, err := tx.Exec("UPDATE gateway_users SET default_tenant_id = COALESCE(NULLIF(default_tenant_id, ''), ?), default_shop_id = COALESCE(NULLIF(default_shop_id, ''), ?), onboarding_completed = TRUE WHERE id = ?", tenantID, shopID, userID); err != nil {
			return err
		}
	}
	return tx.Commit()
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
	user.Name = cleanDisplayText(user.Name)
	user.CompanyName = cleanDisplayText(companyName.String)
	user.Plan = cleanDisplayText(firstNonEmpty(plan.String, "Team"))
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
	if _, err := s.db.Exec("UPDATE gateway_users SET name=COALESCE(NULLIF(?, ''), name), email=COALESCE(NULLIF(?, ''), email), phone=COALESCE(NULLIF(?, ''), phone), company_name=COALESCE(NULLIF(?, ''), company_name) WHERE id=?", cleanDisplayText(input.Name), input.Email, input.Phone, cleanDisplayText(input.CompanyName), userID); err != nil {
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

func (s *MySQLUserStore) indexExists(tableName string, indexName string) (bool, error) {
	var count int
	if err := s.db.QueryRow("SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = ? AND index_name = ?", tableName, indexName).Scan(&count); err != nil {
		return false, err
	}
	return count > 0, nil
}

func upsertTenant(tx *sql.Tx, tenantID string, name string) error {
	_, err := tx.Exec("INSERT INTO gateway_tenants (id, name, status) VALUES (?, ?, 'active') ON DUPLICATE KEY UPDATE name = VALUES(name), status = 'active'", tenantID, cleanDisplayText(name))
	return err
}

func upsertShop(tx *sql.Tx, tenantID string, shopID string, name string, authStatus string) error {
	_, err := tx.Exec("INSERT INTO gateway_shops (tenant_id, id, name, category, platform, shop_type, business_stage, status, auth_status, data_status) VALUES (?, ?, ?, 'unset', 'taobao_tmall', 'brand_owned', 'growth', 'active', ?, 'empty') ON DUPLICATE KEY UPDATE status = 'active', auth_status = VALUES(auth_status)", tenantID, shopID, cleanDisplayText(name), authStatus)
	return err
}

func cleanDisplayText(value string) string {
	trimmed := strings.TrimSpace(value)
	if trimmed == "" {
		return trimmed
	}
	if repaired, ok := repairLatin1Mojibake(trimmed); ok {
		return repaired
	}
	return trimmed
}

func repairLatin1Mojibake(value string) (string, bool) {
	bytes := make([]byte, 0, len(value))
	changed := false
	for _, r := range value {
		if r > 255 {
			return value, false
		}
		if r >= 128 {
			changed = true
		}
		bytes = append(bytes, byte(r))
	}
	if !changed || !utf8.Valid(bytes) {
		return value, false
	}
	repaired := string(bytes)
	if repaired == value {
		return value, false
	}
	return repaired, true
}

func nullIfEmpty(value string) interface{} {
	if value == "" {
		return nil
	}
	return value
}

func gatewayMySQLDSN() string {
	user := os.Getenv("MYSQL_USER")
	password := os.Getenv("MYSQL_PASSWORD")
	host := envOrDefault("MYSQL_HOST", "localhost")
	port := envOrDefault("MYSQL_PORT", "3306")
	database := gatewayMySQLDatabase()
	return fmt.Sprintf("%s:%s@tcp(%s:%s)/%s?parseTime=true&charset=utf8mb4", user, password, host, port, database)
}

func gatewayMySQLDatabase() string {
	return envOrDefault("MYSQL_DATABASE", "ecommerce_demo")
}
