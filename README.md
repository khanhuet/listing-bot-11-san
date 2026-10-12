# Listing Alert Bot - 11 Exchanges
Hỗ trợ: Binance, OKX, MEXC, Bitget, Gate, Upbit, Kucoin, Bybit, HTX, BingX, Lbank

### Chạy
1. Copy .env.example -> .env và điền token
2. docker compose up -d --build
3. Xem log: docker compose logs -f

### Tính năng
- Quét 45s/lần
- Bắn ngay khi có announcement
- AI parser tự bóc giờ trade và hẹn T-10 phút
- Chống bắn trùng bằng SQLite
