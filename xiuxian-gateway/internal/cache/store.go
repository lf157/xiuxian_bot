// Package cache implements Redis data structures for player state.
//
// Design principles:
//   - Hash for structured player data (NOT JSON strings)
//   - Bitmap for boolean states (signin, chapter unlocks)
//   - Sorted sets for leaderboards
//   - Minimal memory footprint per user
package cache

import (
	"context"
	"fmt"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"
)

// ── Redis keys ──────────────────────────────────────────

// player:{uid}         → Hash  (rank, exp, hp, mp, attack, defense, copper, gold, element, current_map …)
// signin:{uid}:{yyyyMM} → Bitmap  (bit N = day N signed, 1-31)
// story:{uid}           → Hash  (chapter_id → current_line)
// story:done:{uid}      → Bitmap  (bit N = chapter index N finished)
// cultivate:{uid}       → Hash  (start_time, gain_per_hour, boost_info)
// leaderboard:exp       → Sorted Set  (member=uid, score=exp)

func PlayerKey(uid string) string       { return "p:" + uid }
func SigninKey(uid, month string) string { return "si:" + uid + ":" + month }
func StoryKey(uid string) string        { return "st:" + uid }
func StoryDoneKey(uid string) string    { return "sd:" + uid }
func CultivateKey(uid string) string    { return "cu:" + uid }

const LeaderboardExp = "lb:exp"

// ── Client ──────────────────────────────────────────────

type Store struct {
	rdb *redis.Client
}

func New(addr, password string, db int) *Store {
	rdb := redis.NewClient(&redis.Options{
		Addr:         addr,
		Password:     password,
		DB:           db,
		PoolSize:     128,
		MinIdleConns: 16,
		ReadTimeout:  500 * time.Millisecond,
		WriteTimeout: 500 * time.Millisecond,
	})
	return &Store{rdb: rdb}
}

func (s *Store) Ping(ctx context.Context) error {
	return s.rdb.Ping(ctx).Err()
}

func (s *Store) Close() error {
	return s.rdb.Close()
}

// ── Player Hash ─────────────────────────────────────────

// SetPlayer stores all fields from a flat map into a Redis Hash.
func (s *Store) SetPlayer(ctx context.Context, uid string, fields map[string]interface{}, ttl time.Duration) error {
	key := PlayerKey(uid)
	pipe := s.rdb.Pipeline()
	pipe.HSet(ctx, key, fields)
	if ttl > 0 {
		pipe.Expire(ctx, key, ttl)
	}
	_, err := pipe.Exec(ctx)
	return err
}

// GetPlayer returns all fields of a player Hash.
func (s *Store) GetPlayer(ctx context.Context, uid string) (map[string]string, error) {
	return s.rdb.HGetAll(ctx, PlayerKey(uid)).Result()
}

// GetPlayerField returns a single field.
func (s *Store) GetPlayerField(ctx context.Context, uid, field string) (string, error) {
	return s.rdb.HGet(ctx, PlayerKey(uid), field).Result()
}

// IncrPlayerField atomically increments a numeric field.
func (s *Store) IncrPlayerField(ctx context.Context, uid, field string, delta int64) (int64, error) {
	return s.rdb.HIncrBy(ctx, PlayerKey(uid), field, delta).Result()
}

// DelPlayer removes the player cache entirely.
func (s *Store) DelPlayer(ctx context.Context, uid string) error {
	return s.rdb.Del(ctx, PlayerKey(uid)).Err()
}

// ── Signin Bitmap ───────────────────────────────────────

// SetSignin marks day (1-31) as signed in the month bitmap.
func (s *Store) SetSignin(ctx context.Context, uid, month string, day int) error {
	key := SigninKey(uid, month)
	pipe := s.rdb.Pipeline()
	pipe.SetBit(ctx, key, int64(day), 1)
	pipe.Expire(ctx, key, 40*24*time.Hour) // keep ~40 days
	_, err := pipe.Exec(ctx)
	return err
}

// GetSignin checks if a specific day is signed.
func (s *Store) GetSignin(ctx context.Context, uid, month string, day int) (bool, error) {
	v, err := s.rdb.GetBit(ctx, SigninKey(uid, month), int64(day)).Result()
	return v == 1, err
}

// CountSignin returns total signed days in the month.
func (s *Store) CountSignin(ctx context.Context, uid, month string) (int64, error) {
	return s.rdb.BitCount(ctx, SigninKey(uid, month), nil).Result()
}

// ── Story Progress Hash ─────────────────────────────────

// SetStoryLine stores the current reading position for a chapter.
func (s *Store) SetStoryLine(ctx context.Context, uid, chapterID string, line int) error {
	return s.rdb.HSet(ctx, StoryKey(uid), chapterID, strconv.Itoa(line)).Err()
}

// GetStoryLine returns the current reading position.
func (s *Store) GetStoryLine(ctx context.Context, uid, chapterID string) (int, error) {
	v, err := s.rdb.HGet(ctx, StoryKey(uid), chapterID).Result()
	if err == redis.Nil {
		return 0, nil
	}
	if err != nil {
		return 0, err
	}
	n, _ := strconv.Atoi(v)
	return n, nil
}

// SetStoryDone marks a chapter (by index) as finished.
func (s *Store) SetStoryDone(ctx context.Context, uid string, chapterIndex int) error {
	return s.rdb.SetBit(ctx, StoryDoneKey(uid), int64(chapterIndex), 1).Err()
}

// IsStoryDone checks if a chapter is finished.
func (s *Store) IsStoryDone(ctx context.Context, uid string, chapterIndex int) (bool, error) {
	v, err := s.rdb.GetBit(ctx, StoryDoneKey(uid), int64(chapterIndex)).Result()
	return v == 1, err
}

// ── Cultivation Hash ────────────────────────────────────

// SetCultivation stores active cultivation session.
func (s *Store) SetCultivation(ctx context.Context, uid string, startTime int64, gainPerHour int, boostInfo string) error {
	key := CultivateKey(uid)
	return s.rdb.HSet(ctx, key, map[string]interface{}{
		"start":  startTime,
		"gain":   gainPerHour,
		"boost":  boostInfo,
	}).Err()
}

// GetCultivation returns active session, empty map if none.
func (s *Store) GetCultivation(ctx context.Context, uid string) (map[string]string, error) {
	return s.rdb.HGetAll(ctx, CultivateKey(uid)).Result()
}

// DelCultivation removes the session on end.
func (s *Store) DelCultivation(ctx context.Context, uid string) error {
	return s.rdb.Del(ctx, CultivateKey(uid)).Err()
}

// ── Leaderboard (Sorted Set) ────────────────────────────

// UpdateLeaderboard sets a player's exp score.
func (s *Store) UpdateLeaderboard(ctx context.Context, uid string, exp float64) error {
	return s.rdb.ZAdd(ctx, LeaderboardExp, redis.Z{Score: exp, Member: uid}).Err()
}

// TopN returns the top N players by exp.
func (s *Store) TopN(ctx context.Context, n int64) ([]redis.Z, error) {
	return s.rdb.ZRevRangeWithScores(ctx, LeaderboardExp, 0, n-1).Result()
}

// Rank returns a player's rank (0-based).
func (s *Store) Rank(ctx context.Context, uid string) (int64, error) {
	rank, err := s.rdb.ZRevRank(ctx, LeaderboardExp, uid).Result()
	if err == redis.Nil {
		return -1, nil
	}
	return rank, err
}

// ── Helpers ─────────────────────────────────────────────

// MapToPlayerFields converts a Python API response into flat Hash fields.
// Only includes the fields we care about caching.
func MapToPlayerFields(m map[string]interface{}) map[string]interface{} {
	out := make(map[string]interface{}, 20)
	pick := []string{
		"rank", "exp", "hp", "max_hp", "mp", "max_mp",
		"attack", "defense", "copper", "gold",
		"element", "current_map", "in_game_username",
		"stamina", "crit_rate", "state",
	}
	for _, k := range pick {
		if v, ok := m[k]; ok {
			out[k] = fmt.Sprint(v)
		}
	}
	return out
}
