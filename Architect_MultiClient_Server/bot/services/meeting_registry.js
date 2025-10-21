// services/meeting_registry.js

class MeetingRegistry {
  constructor() {
    // { meetingCode: Set(userIds) }
    this.meetings = new Map();
  }

  addUser(meetingCode, userId) {
    if (!this.meetings.has(meetingCode)) {
      this.meetings.set(meetingCode, new Set());
    }
    this.meetings.get(meetingCode).add(userId);
    return this.getUsers(meetingCode);
  }

  removeUser(meetingCode, userId) {
    if (this.meetings.has(meetingCode)) {
      this.meetings.get(meetingCode).delete(userId);

      // Cleanup empty meetings
      if (this.meetings.get(meetingCode).size === 0) {
        this.meetings.delete(meetingCode);
      }
    }
  }

  getUsers(meetingCode) {
    return this.meetings.has(meetingCode)
      ? Array.from(this.meetings.get(meetingCode))
      : [];
  }

  hasUser(meetingCode, userId) {
    return this.meetings.has(meetingCode)
      && this.meetings.get(meetingCode).has(userId);
  }

  clearMeeting(meetingCode) {
    this.meetings.delete(meetingCode);
  }

  getAllMeetings() {
    const result = {};
    this.meetings.forEach((users, meetingCode) => {
      result[meetingCode] = Array.from(users);
    });
    return result;
  }

  getStats() {
    return {
      totalMeetings: this.meetings.size,
      totalUsers: Array.from(this.meetings.values())
        .reduce((sum, users) => sum + users.size, 0)
    };
  }


  getUserCount(meetingCode) {
    if (!this.meetings.has(meetingCode)) {
      return 0;
    }
    return this.meetings.get(meetingCode).size;
  }
}
module.exports = MeetingRegistry;