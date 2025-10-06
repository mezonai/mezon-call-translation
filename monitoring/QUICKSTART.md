# Quick Start Guide - Monitoring Stack

## 🚀 5 phút để setup Prometheus + Grafana

### Bước 1: Khởi động Main Application (nếu chưa chạy)

```bash
# Từ thư mục gốc
cd e:\NCC\mezon-call-translation
docker-compose up -d
```

### Bước 2: Khởi động Monitoring Stack

```bash
# Di chuyển vào thư mục monitoring
cd monitoring

# Windows
.\scripts\start.ps1

# Linux/Mac
chmod +x scripts/*.sh
./scripts/start.sh
```

### Bước 3: Truy cập Grafana

1. Mở browser: http://localhost:3000
2. Login:
   - Username: `admin`
   - Password: `admin`
3. Vào **Dashboards** → **Browse** → **Mezon Call Translation** → **Mezon Call Translation - Overview**

### Bước 4: Xem Metrics

Dashboard sẽ hiển thị:
- ✅ WebSocket connections
- ✅ HTTP request rate
- ✅ CPU & Memory usage
- ✅ Transcription metrics
- ✅ Error rates
- ✅ Queue sizes

---

## 🔍 Verify Setup

### Check Prometheus Targets

```bash
# Windows
.\scripts\check-targets.ps1

# Linux/Mac
./scripts/check-targets.sh
```

**Hoặc mở browser:**
- Prometheus: http://localhost:9090/targets
- Grafana: http://localhost:3000
- AlertManager: http://localhost:9093

---

## 📊 Quick Queries

Vào Prometheus UI (http://localhost:9090) và thử các queries:

```promql
# Số WebSocket connections hiện tại
ws_connections_current

# HTTP request rate (requests/second)
rate(http_requests_total[5m])

# CPU usage
cpu_usage_percent

# Memory usage
memory_usage_bytes

# Error rate
rate(ws_errors_total[5m])

# Transcription duration (95th percentile)
histogram_quantile(0.95, rate(transcription_duration_seconds_bucket[5m]))
```

---

## 🛑 Stop Monitoring

```bash
# Windows
.\scripts\stop.ps1

# Linux/Mac
./scripts/stop.sh
```

---

## ⚠️ Troubleshooting

### Prometheus không thấy targets?

```bash
# Check network
docker network ls | grep mezon

# Check connectivity
docker exec mezon-prometheus ping server

# Check logs
docker-compose logs prometheus
```

### Grafana không hiển thị data?

1. Check Prometheus targets: http://localhost:9090/targets
2. Verify datasource: Grafana → Configuration → Data Sources
3. Check time range trong dashboard (góc trên bên phải)

### Container không start?

```bash
# Check logs
docker-compose logs

# Restart
docker-compose restart

# Full restart
docker-compose down
docker-compose up -d
```

---

## 📚 Next Steps

- 📖 Đọc [README.md](README.md) để hiểu chi tiết
- 🔧 Đọc [SCRAPING-STRATEGY.md](docs/SCRAPING-STRATEGY.md) về cách Prometheus thu thập metrics
- 🚨 Cấu hình alerts trong `alertmanager/alertmanager.yml`
- 🔐 Đổi password Grafana trong `.env`

---

## 🎯 Key URLs

| Service | URL | Credentials |
|---------|-----|-------------|
| **Grafana** | http://localhost:3000 | admin / admin |
| **Prometheus** | http://localhost:9090 | - |
| **AlertManager** | http://localhost:9093 | - |
| **Main App** | http://localhost:8000 | - |
| **Metrics Endpoint** | http://localhost:8000/metrics | - |

---

## 💡 Tips

1. **Đổi password Grafana ngay:**
   - Sửa file `.env`: `GRAFANA_ADMIN_PASSWORD=your-password`
   - Restart: `docker-compose restart grafana`

2. **Scale server để test:**
   ```bash
   cd ..
   docker-compose up -d --scale server=3
   ```

3. **Monitor real-time:**
   - Set dashboard refresh: 5s hoặc 10s
   - Vào Prometheus → Graph để query real-time

4. **Export dashboard:**
   - Grafana → Dashboard → Share → Export → Save to file

---

## ✅ Success Checklist

- [ ] Main application đang chạy
- [ ] Monitoring stack đã start
- [ ] Prometheus targets status = UP
- [ ] Grafana dashboard hiển thị data
- [ ] Đã đổi password Grafana
- [ ] Alerts đang hoạt động (check AlertManager UI)

**Xong! Bạn đã có monitoring stack hoàn chỉnh! 🎉**
