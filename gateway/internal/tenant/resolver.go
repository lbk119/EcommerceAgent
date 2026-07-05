package tenant

import (
	"errors"
	"strings"

	"DeepAgent/gateway/internal/auth"
)

// TenantContext is the trusted identity envelope produced by the gateway.
// Middleware stores it in Gin context and the reverse proxy forwards it to
// Python Brain as X-* headers. Brain should prefer these headers over request body IDs.
type TenantContext struct {
	TenantID    string
	UserID      string
	ShopID      string
	Roles       []string
	Permissions []string
}

// Resolve applies the tenant priority rule:
// 1. X-Tenant-ID header, 2. JWT default_tenant_id, 3. user's only tenant.
// It also checks tenant and shop membership before context reaches Python Brain.
func Resolve(user auth.User, requestedTenantID string, requestedShopID string) (TenantContext, error) {
	tenantID := strings.TrimSpace(requestedTenantID)
	if tenantID == "" {
		tenantID = user.DefaultTenantID
	}
	if tenantID == "" && len(user.TenantIDs) == 1 {
		tenantID = user.TenantIDs[0]
	}
	if tenantID == "" {
		return TenantContext{}, errors.New("tenant required")
	}
	if !contains(user.TenantIDs, tenantID) {
		return TenantContext{}, errors.New("tenant forbidden")
	}

	shopID := strings.TrimSpace(requestedShopID)
	if shopID == "" {
		shopID = user.DefaultShopID
	}
	if shopID != "" && len(user.ShopIDs) > 0 && !contains(user.ShopIDs, shopID) {
		return TenantContext{}, errors.New("shop forbidden")
	}

	return TenantContext{
		TenantID:    tenantID,
		UserID:      user.ID,
		ShopID:      shopID,
		Roles:       user.Roles,
		Permissions: user.Permissions,
	}, nil
}

func contains(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}
