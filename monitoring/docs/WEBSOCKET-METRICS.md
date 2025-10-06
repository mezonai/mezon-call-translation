# WebSocket Metrics Guide

## 📊 Tổng quan

Hệ thống giờ đây track đầy đủ metrics cho WebSocket connections, bao gồm:

- Connection lifecycle (connect/disconnect)
- Data transfer (bytes sent/received)
- Connection duration
- Disconnect codes
- Errors

---

## 🎯 Metrics Available

### 1. **ws_connections_current** (Gauge)
Số lượng WebSocket connections hiện tại

```promql
# Current connections
ws_connections_current

# Alert if too many connections
ws_connections_current > 45
```

**Use cases:**
- Monitor active connections
- Capacity planning
- Alert on approaching limits

---

### 2. **ws_messages_total** (Counter)
Tổng số messages sent/received

**Labels:**
- `direction`: "in" hoặc "out"
- `session_id`: Session ID

```promql
# Messages per second (incoming)
rate(ws_messages_total{direction="in"}[5m])

# Messages per second (outgoing)
rate(ws_messages_total{direction="out"}[5m])

# Total messages by session
sum(ws_messages_total) by (session_id)
```

**Use cases:**
- Monitor message throughput
- Identify busy sessions
- Detect message storms

---

### 3. **ws_disconnects_total** (Counter)
Tổng số disconnects theo code

**Labels:**
- `code`: WebSocket close code (1000, 1001, 1006, etc.)

```promql
# Disconnect rate
rate(ws_disconnects_total[5m])

# Disconnects by code
sum(ws_disconnects_total) by (code)

# Abnormal disconnects (code 1006)
rate(ws_disconnects_total{code="1006"}[5m])
```

**WebSocket Close Codes:**
- `1000`: Normal closure
- `1001`: Going away
- `1006`: Abnormal closure (no close frame)
- `1011`: Server error
- `1012`: Service restart

**Use cases:**
- Monitor disconnect patterns
- Identify connection issues
- Alert on abnormal disconnects

---

### 4. **ws_connection_duration_seconds** (Histogram)
Thời gian connection tồn tại

**Buckets:** 1s, 5s, 10s, 30s, 1m, 2m, 5m, 10m, 30m, 1h

```promql
# Average connection duration
rate(ws_connection_duration_seconds_sum[5m]) / rate(ws_connection_duration_seconds_count[5m])

# 95th percentile duration
histogram_quantile(0.95, rate(ws_connection_duration_seconds_bucket[5m]))

# 50th percentile (median)
histogram_quantile(0.50, rate(ws_connection_duration_seconds_bucket[5m]))

# Connections shorter than 10 seconds
rate(ws_connection_duration_seconds_bucket{le="10"}[5m])
```

**Use cases:**
- Understand connection patterns
- Identify short-lived connections
- Monitor session stability

---

### 5. **ws_bytes_received_total** (Counter)
Tổng số bytes nhận được qua WebSocket

**Labels:**
- `session_id`: Session ID

```promql
# Bytes per second received
rate(ws_bytes_received_total[5m])

# Total bytes by session
sum(ws_bytes_received_total) by (session_id)

# MB per minute
rate(ws_bytes_received_total[1m]) / 1024 / 1024

# Top 5 sessions by data received
topk(5, rate(ws_bytes_received_total[5m]))
```

**Use cases:**
- Monitor bandwidth usage
- Identify heavy users
- Capacity planning

---

### 6. **ws_bytes_sent_total** (Counter)
Tổng số bytes gửi đi qua WebSocket

**Labels:**
- `session_id`: Session ID

```promql
# Bytes per second sent
rate(ws_bytes_sent_total[5m])

# Total bandwidth (sent + received)
rate(ws_bytes_sent_total[5m]) + rate(ws_bytes_received_total[5m])

# Ratio sent/received
rate(ws_bytes_sent_total[5m]) / rate(ws_bytes_received_total[5m])
```

**Use cases:**
- Monitor outbound bandwidth
- Analyze data flow patterns
- Cost estimation

---

### 7. **ws_errors_total** (Counter)
Tổng số WebSocket errors

**Labels:**
- `type`: Error type (e.g., "send", "receive")

```promql
# Error rate
rate(ws_errors_total[5m])

# Errors by type
sum(ws_errors_total) by (type)

# Error percentage
rate(ws_errors_total[5m]) / rate(ws_messages_total[5m]) * 100
```

**Use cases:**
- Monitor error rates
- Identify problematic operations
- Alert on high error rates

---

## 📈 Useful Queries

### Connection Health

```promql
# Connection success rate (connections that last > 10s)
(
  rate(ws_connection_duration_seconds_count[5m]) 
  - rate(ws_connection_duration_seconds_bucket{le="10"}[5m])
) / rate(ws_connection_duration_seconds_count[5m]) * 100
```

### Bandwidth Usage

```promql
# Total bandwidth in MB/s
(
  rate(ws_bytes_sent_total[5m]) + 
  rate(ws_bytes_received_total[5m])
) / 1024 / 1024
```

### Connection Stability

```promql
# Percentage of normal disconnects
rate(ws_disconnects_total{code="1000"}[5m]) / 
rate(ws_disconnects_total[5m]) * 100
```

### Active Sessions

```promql
# Number of active sessions
count(rate(ws_bytes_received_total[1m]) > 0)
```

### Data Flow Ratio

```promql
# How much data we send vs receive
rate(ws_bytes_sent_total[5m]) / rate(ws_bytes_received_total[5m])
```

---

## 🚨 Alert Rules

### High Disconnect Rate

```yaml
- alert: HighWebSocketDisconnectRate
  expr: rate(ws_disconnects_total[5m]) > 0.5
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "High WebSocket disconnect rate"
    description: "{{ $value }} disconnects/sec"
```

### Abnormal Disconnects

```yaml
- alert: HighAbnormalDisconnects
  expr: rate(ws_disconnects_total{code="1006"}[5m]) > 0.1
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "High abnormal disconnect rate"
    description: "{{ $value }} abnormal disconnects/sec (code 1006)"
```

### Connection Limit Approaching

```yaml
- alert: WebSocketConnectionLimitApproaching
  expr: ws_connections_current > 45
  for: 2m
  labels:
    severity: warning
  annotations:
    summary: "Approaching WebSocket connection limit"
    description: "{{ $value }} connections (limit: 50)"
```

### High Bandwidth Usage

```yaml
- alert: HighWebSocketBandwidth
  expr: |
    (
      rate(ws_bytes_sent_total[5m]) + 
      rate(ws_bytes_received_total[5m])
    ) / 1024 / 1024 > 100
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "High WebSocket bandwidth usage"
    description: "{{ $value }} MB/s"
```

### Short-lived Connections

```yaml
- alert: TooManyShortLivedConnections
  expr: |
    rate(ws_connection_duration_seconds_bucket{le="10"}[5m]) /
    rate(ws_connection_duration_seconds_count[5m]) > 0.5
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "Too many short-lived connections"
    description: "{{ $value | humanizePercentage }} of connections last < 10s"
```

---

## 📊 Grafana Dashboard Panels

### Panel 1: Active Connections

```promql
ws_connections_current
```

**Visualization:** Gauge  
**Thresholds:** Yellow at 40, Red at 45

---

### Panel 2: Connection Rate

```promql
# New connections per minute
rate(ws_connection_duration_seconds_count[1m]) * 60
```

**Visualization:** Graph  
**Unit:** connections/min

---

### Panel 3: Disconnect Codes Distribution

```promql
sum(ws_disconnects_total) by (code)
```

**Visualization:** Pie Chart  
**Legend:** Show code meanings

---

### Panel 4: Bandwidth Usage

```promql
# Sent
rate(ws_bytes_sent_total[5m]) / 1024 / 1024

# Received
rate(ws_bytes_received_total[5m]) / 1024 / 1024
```

**Visualization:** Graph (stacked area)  
**Unit:** MB/s

---

### Panel 5: Connection Duration Distribution

```promql
histogram_quantile(0.50, rate(ws_connection_duration_seconds_bucket[5m]))
histogram_quantile(0.95, rate(ws_connection_duration_seconds_bucket[5m]))
histogram_quantile(0.99, rate(ws_connection_duration_seconds_bucket[5m]))
```

**Visualization:** Graph  
**Legend:** p50, p95, p99

---

### Panel 6: Top Sessions by Data

```promql
topk(10, rate(ws_bytes_received_total[5m]))
```

**Visualization:** Table  
**Columns:** Session ID, Bytes/sec

---

### Panel 7: Error Rate

```promql
rate(ws_errors_total[5m])
```

**Visualization:** Graph  
**Alert:** Show when > 0.1

---

## 🔍 Debugging Scenarios

### Scenario 1: High Disconnect Rate

**Check:**
```promql
# What codes are causing disconnects?
sum(rate(ws_disconnects_total[5m])) by (code)

# Are connections short-lived?
histogram_quantile(0.50, rate(ws_connection_duration_seconds_bucket[5m]))

# Any errors?
rate(ws_errors_total[5m])
```

**Possible causes:**
- Network issues (code 1006)
- Client timeouts
- Server errors (code 1011)

---

### Scenario 2: High Bandwidth Usage

**Check:**
```promql
# Which sessions use most bandwidth?
topk(5, rate(ws_bytes_received_total[5m]))

# Is it incoming or outgoing?
rate(ws_bytes_sent_total[5m]) / rate(ws_bytes_received_total[5m])

# How many active sessions?
count(rate(ws_bytes_received_total[1m]) > 0)
```

**Possible causes:**
- Too many concurrent sessions
- Large audio chunks
- Inefficient encoding

---

### Scenario 3: Connection Instability

**Check:**
```promql
# Average connection duration
rate(ws_connection_duration_seconds_sum[5m]) / rate(ws_connection_duration_seconds_count[5m])

# Percentage of abnormal disconnects
rate(ws_disconnects_total{code="1006"}[5m]) / rate(ws_disconnects_total[5m])

# Connection success rate
(rate(ws_connection_duration_seconds_count[5m]) - rate(ws_connection_duration_seconds_bucket{le="10"}[5m])) / rate(ws_connection_duration_seconds_count[5m])
```

**Possible causes:**
- Network instability
- Load balancer issues
- Client-side problems

---

## 🎓 Best Practices

### 1. Monitor Connection Lifecycle

Track the full lifecycle:
- Connection established → `ws_connections.inc()`
- Data transfer → `ws_bytes_*`
- Disconnect → `ws_connections.dec()`, `ws_disconnects.inc()`

### 2. Use Labels Wisely

- Use `session_id` for per-session metrics
- Use `code` for disconnect analysis
- Avoid high-cardinality labels (like `client_id`)

### 3. Set Appropriate Alerts

- Alert on abnormal disconnects (code 1006)
- Alert on approaching connection limits
- Alert on high error rates

### 4. Analyze Patterns

- Look at disconnect code distribution
- Monitor connection duration patterns
- Track bandwidth usage trends

### 5. Correlate Metrics

Combine WebSocket metrics with:
- HTTP metrics (for API calls)
- Transcription metrics (for processing)
- System metrics (CPU/memory)

---

## 📝 Summary

### ✅ Now Tracking:

- ✅ Active connections (`ws_connections_current`)
- ✅ Messages sent/received (`ws_messages_total`)
- ✅ Disconnects by code (`ws_disconnects_total`)
- ✅ Connection duration (`ws_connection_duration_seconds`)
- ✅ Bytes sent/received (`ws_bytes_*_total`)
- ✅ Errors (`ws_errors_total`)

### 🎯 Key Insights:

- Connection health and stability
- Bandwidth usage patterns
- Disconnect reasons
- Session activity
- Error rates

### 📊 Next Steps:

1. Restart server để apply changes
2. Connect WebSocket clients
3. View metrics: http://localhost:8000/metrics
4. Query in Prometheus: http://localhost:9090
5. Visualize in Grafana: http://localhost:3000
