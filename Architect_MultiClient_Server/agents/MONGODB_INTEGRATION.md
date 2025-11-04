# MongoDB Transcript Storage Integration

**Date:** November 4, 2025  
**Feature:** Transcript storage in MongoDB

---

## Overview

Agent bây giờ lưu tất cả transcripts vào MongoDB thay vì chỉ log ra console. Điều này cho phép:

- ✅ **Persistent storage** - Transcripts được lưu vĩnh viễn
- ✅ **Query & Analysis** - Có thể query transcripts theo session, participant, time
- ✅ **Export** - Dễ dàng export transcripts ra file
- ✅ **Statistics** - Phân tích thống kê về meetings

---

## Architecture

```
┌─────────────────┐
│  LiveKit Agent  │
│                 │
│  ┌───────────┐  │
│  │Transcript │  │
│  │ Manager   │  │
│  └─────┬─────┘  │
│        │        │
└────────┼────────┘
         │
         │ saves transcript
         ▼
┌─────────────────┐
│  MongoDB        │
│  Service        │
│  (motor async)  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│    MongoDB      │
│                 │
│  Database:      │
│  mezon_transcripts
│                 │
│  Collection:    │
│  transcripts    │
└─────────────────┘
```

---

## Database Schema

### Collection: `transcripts`

```json
{
  "_id": ObjectId("..."),
  "session_id": "meeting-room-123",
  "participant_identity": "user_abc",
  "participant_name": "John Doe",
  "text": "Hello everyone, how are you?",
  "is_final": true,
  "segments": [
    {
      "text": "Hello everyone, how are you?",
      "start": 0.0,
      "end": 2.5,
      "completed": true
    }
  ],
  "language": "en",
  "seq": 5,
  "timestamp": ISODate("2025-11-04T10:30:45.123Z"),
  "metadata": {
    "room_name": "meeting-room-123",
    "agent_name": "Vosk-Transcription-Agent"
  }
}
```

### Indexes

Tự động tạo indexes để tối ưu query performance:

```javascript
// Compound index for session queries
db.transcripts.createIndex({ "session_id": 1, "timestamp": -1 })

// Index for participant queries
db.transcripts.createIndex({ "participant_identity": 1 })

// Index for filtering final transcripts
db.transcripts.createIndex({ "is_final": 1 })
```

---

## Configuration

### Environment Variables

```bash
# Enable/disable MongoDB storage
ENABLE_MONGODB=true

# MongoDB connection URI
MONGODB_URI=mongodb://mongodb:27017

# Database name
MONGODB_DATABASE=mezon_transcripts

# Collection name
MONGODB_COLLECTION=transcripts
```

### Docker Compose

MongoDB service được thêm vào `docker-compose.yml`:

```yaml
mongodb:
  image: mongo:7
  container_name: mezon-mongodb
  ports:
    - "27017:27017"
  environment:
    - MONGO_INITDB_DATABASE=mezon_transcripts
  volumes:
    - mongodb_data:/data/db
    - mongodb_config:/data/configdb
  healthcheck:
    test: ["CMD", "mongosh", "--eval", "db.adminCommand('ping')"]
```

---

## Usage

### Service Methods

#### Save Transcript

```python
from src.services.mongodb_service import get_mongodb_service

mongodb = get_mongodb_service()
await mongodb.connect()

doc_id = await mongodb.save_transcript(
    session_id="meeting-123",
    participant_identity="user_abc",
    participant_name="John Doe",
    text="Hello everyone",
    is_final=True,
    segments=[...],
    language="en",
    seq=1
)
```

#### Get Session Transcripts

```python
# Get all final transcripts for a session
transcripts = await mongodb.get_session_transcripts(
    session_id="meeting-123",
    only_final=True,
    limit=100
)

for transcript in transcripts:
    print(f"{transcript['participant_name']}: {transcript['text']}")
```

#### Get Participant Transcripts

```python
# Get transcripts from specific participant
transcripts = await mongodb.get_participant_transcripts(
    session_id="meeting-123",
    participant_identity="user_abc",
    only_final=True
)
```

#### Delete Session Transcripts

```python
# Delete all transcripts for a session
deleted_count = await mongodb.delete_session_transcripts("meeting-123")
print(f"Deleted {deleted_count} transcripts")
```

#### Get Statistics

```python
# Get database statistics
stats = await mongodb.get_stats()
print(f"Total transcripts: {stats['total_transcripts']}")
print(f"Total sessions: {stats['total_sessions']}")
print(f"Total participants: {stats['total_participants']}")
```

---

## Query Examples

### MongoDB Shell

```javascript
// Connect to database
use mezon_transcripts

// Find all transcripts in a session
db.transcripts.find({ 
  session_id: "meeting-123",
  is_final: true 
}).sort({ timestamp: 1 })

// Find transcripts by participant
db.transcripts.find({
  session_id: "meeting-123",
  participant_identity: "user_abc"
})

// Get transcript count per participant
db.transcripts.aggregate([
  { $match: { session_id: "meeting-123", is_final: true } },
  { $group: { 
    _id: "$participant_identity", 
    count: { $sum: 1 },
    participant_name: { $first: "$participant_name" }
  }}
])

// Get full conversation in chronological order
db.transcripts.find({
  session_id: "meeting-123",
  is_final: true
}).sort({ timestamp: 1 }).pretty()

// Search for specific text
db.transcripts.find({
  session_id: "meeting-123",
  text: /hello/i
})

// Get transcripts in time range
db.transcripts.find({
  session_id: "meeting-123",
  timestamp: {
    $gte: ISODate("2025-11-04T10:00:00Z"),
    $lt: ISODate("2025-11-04T11:00:00Z")
  }
})

// Export to JSON
mongoexport --db mezon_transcripts --collection transcripts \
  --query '{"session_id":"meeting-123","is_final":true}' \
  --out meeting-123-transcript.json
```

---

## Testing

### Test MongoDB Connection

```python
import asyncio
from src.services.mongodb_service import get_mongodb_service

async def test_mongodb():
    mongodb = get_mongodb_service()
    
    # Connect
    connected = await mongodb.connect()
    print(f"Connected: {connected}")
    
    # Save test transcript
    doc_id = await mongodb.save_transcript(
        session_id="test-session",
        participant_identity="test-user",
        participant_name="Test User",
        text="This is a test transcript",
        is_final=True
    )
    print(f"Saved: {doc_id}")
    
    # Retrieve transcripts
    transcripts = await mongodb.get_session_transcripts("test-session")
    print(f"Retrieved {len(transcripts)} transcripts")
    
    # Get stats
    stats = await mongodb.get_stats()
    print(f"Stats: {stats}")
    
    # Cleanup
    deleted = await mongodb.delete_session_transcripts("test-session")
    print(f"Deleted {deleted} transcripts")
    
    await mongodb.disconnect()

# Run test
asyncio.run(test_mongodb())
```

---

## Deployment

### Production Setup

1. **Update .env file:**

```bash
ENABLE_MONGODB=true
MONGODB_URI=mongodb://mongodb:27017
MONGODB_DATABASE=mezon_transcripts
MONGODB_COLLECTION=transcripts
```

2. **Start services:**

```bash
docker-compose up -d
```

3. **Verify MongoDB:**

```bash
# Check MongoDB is running
docker ps | grep mongodb

# Check MongoDB logs
docker logs mezon-mongodb

# Connect to MongoDB
docker exec -it mezon-mongodb mongosh
```

4. **Test transcript storage:**

```bash
# Check agent logs for MongoDB connection
docker logs mezon-livekit-agent | grep MongoDB

# Should see:
# ✅ Connected to MongoDB: mezon_transcripts.transcripts
```

### Backup & Restore

```bash
# Backup database
docker exec mezon-mongodb mongodump \
  --db mezon_transcripts \
  --out /data/backup

# Restore database
docker exec mezon-mongodb mongorestore \
  --db mezon_transcripts \
  /data/backup/mezon_transcripts
```

---

## Benefits

### Before (Only Logging)
```
❌ Transcripts chỉ hiển thị trong logs
❌ Không thể query hoặc phân tích
❌ Mất transcript khi restart container
❌ Không thể export
```

### After (MongoDB Storage)
```
✅ Transcripts được lưu vĩnh viễn
✅ Query theo session, participant, time
✅ Persistent storage với volumes
✅ Dễ dàng export ra JSON/CSV
✅ Có thể phân tích statistics
✅ Full-text search support
```

---

## Monitoring

### Check MongoDB Health

```bash
# Via Docker
docker exec mezon-mongodb mongosh --eval "db.adminCommand('ping')"

# Via Health endpoint
curl http://localhost:8000/health
```

### Monitor Storage

```bash
# Check database size
docker exec mezon-mongodb mongosh --eval "
  db.getSiblingDB('mezon_transcripts').stats()
"

# Check collection count
docker exec mezon-mongodb mongosh --eval "
  db.getSiblingDB('mezon_transcripts').transcripts.countDocuments({})
"
```

---

## Troubleshooting

### MongoDB Connection Failed

1. Check MongoDB is running:
```bash
docker ps | grep mongodb
```

2. Check MongoDB logs:
```bash
docker logs mezon-mongodb
```

3. Test connection:
```bash
docker exec -it mezon-mongodb mongosh
```

### Agent Cannot Connect to MongoDB

1. Check environment variables in agent container:
```bash
docker exec mezon-livekit-agent env | grep MONGODB
```

2. Check agent logs:
```bash
docker logs mezon-livekit-agent | grep MongoDB
```

3. Verify network connectivity:
```bash
docker exec mezon-livekit-agent ping mongodb
```

### Disable MongoDB

If you need to disable MongoDB temporarily:

```bash
# In .env file
ENABLE_MONGODB=false

# Restart agent
docker-compose restart agent
```

---

## Future Enhancements

- [ ] Add Redis cache for recent transcripts
- [ ] Implement transcript aggregation/summarization
- [ ] Add transcript search API endpoint
- [ ] Support multiple languages
- [ ] Add export to different formats (PDF, DOCX, etc.)
- [ ] Implement retention policies
- [ ] Add encryption for sensitive transcripts

---

**Created by:** GitHub Copilot  
**Date:** November 4, 2025
