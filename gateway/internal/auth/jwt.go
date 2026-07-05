package auth

import (
	"errors"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

// Claims 是网关签发给前端的 JWT 载荷。
// 这里刻意只保存身份、租户成员关系和粗粒度角色，不保存具体 API 权限；
// API 权限统一交给 Casbin policy 判断，避免 token 里的权限和策略文件发生分裂。
type Claims struct {
	Name            string   `json:"name"`
	TenantIDs       []string `json:"tenant_ids"`
	DefaultTenantID string   `json:"default_tenant_id"`
	ShopIDs         []string `json:"shop_ids"`
	DefaultShopID   string   `json:"default_shop_id"`
	Roles           []string `json:"roles"`
	jwt.RegisteredClaims
}

type TokenManager struct {
	secret []byte
	ttl    time.Duration
}

func NewTokenManager(secret string, ttl time.Duration) (*TokenManager, error) {
	if strings.TrimSpace(secret) == "" {
		return nil, errors.New("jwt secret is required")
	}
	return &TokenManager{secret: []byte(secret), ttl: ttl}, nil
}

func (m *TokenManager) Issue(user User) (string, int64, error) {
	now := time.Now()
	claims := Claims{
		Name:            user.Name,
		TenantIDs:       user.TenantIDs,
		DefaultTenantID: user.DefaultTenantID,
		ShopIDs:         user.ShopIDs,
		DefaultShopID:   user.DefaultShopID,
		Roles:           user.Roles,
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   user.ID,
			IssuedAt:  jwt.NewNumericDate(now),
			ExpiresAt: jwt.NewNumericDate(now.Add(m.ttl)),
		},
	}
	token, err := jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString(m.secret)
	if err != nil {
		return "", 0, err
	}
	return token, int64(m.ttl.Seconds()), nil
}

func (m *TokenManager) Parse(token string) (Claims, error) {
	var claims Claims
	parsedToken, err := jwt.ParseWithClaims(token, &claims, func(parsedToken *jwt.Token) (interface{}, error) {
		// 本地阶段只接受 HS256，防止客户端把 alg 换成其他算法绕过签名校验。
		if parsedToken.Method != jwt.SigningMethodHS256 {
			return nil, errors.New("unexpected signing method")
		}
		return m.secret, nil
	})
	if err != nil {
		if errors.Is(err, jwt.ErrTokenExpired) {
			return Claims{}, errors.New("token expired")
		}
		return Claims{}, errors.New("invalid token")
	}
	if parsedToken == nil || !parsedToken.Valid {
		return Claims{}, errors.New("invalid token")
	}
	return claims, nil
}

func BearerToken(authorization string) (string, bool) {
	parts := strings.SplitN(strings.TrimSpace(authorization), " ", 2)
	if len(parts) != 2 || !strings.EqualFold(parts[0], "Bearer") || strings.TrimSpace(parts[1]) == "" {
		return "", false
	}
	return strings.TrimSpace(parts[1]), true
}
