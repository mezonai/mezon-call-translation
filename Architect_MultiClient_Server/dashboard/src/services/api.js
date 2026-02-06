import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
const API_KEY = import.meta.env.VITE_API_KEY || '';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
    ...(API_KEY && { 'x-api-key': API_KEY })
  }
});

// Room APIs
export const getRooms = async (params = {}) => {
  const { limit = 20, skip = 0, status = null } = params;
  const queryParams = new URLSearchParams({
    limit,
    skip,
    ...(status && { status })
  });
  
  const response = await apiClient.get(`/api/transcripts/rooms?${queryParams}`);
  return response.data;
};

export const getRoomByName = async (roomName) => {
  const response = await apiClient.get(`/api/transcripts/rooms/${roomName}`);
  return response.data;
};

export const getRoomById = async (roomId) => {
  const response = await apiClient.get(`/api/transcripts/rooms/id/${roomId}`);
  return response.data;
};

export const getRoomStatistics = async (roomName) => {
  const response = await apiClient.get(`/api/transcripts/rooms/${roomName}/statistics`);
  return response.data;
};

export const getRoomStatisticsById = async (roomId) => {
  const response = await apiClient.get(`/api/transcripts/rooms/id/${roomId}/statistics`);
  return response.data;
};

// Summary APIs
export const getSummaryByRoom = async (roomName, startTime = null, endTime = null) => {
  const queryParams = new URLSearchParams();
  if (startTime) queryParams.append('start_time', startTime);
  if (endTime) queryParams.append('end_time', endTime);
  
  const query = queryParams.toString();
  const url = `/api/summary/room/${roomName}${query ? `?${query}` : ''}`;
  const response = await apiClient.get(url);
  return response.data;
};

export const getSummaryByRoomId = async (roomId) => {
  const response = await apiClient.get(`/api/summary/room/id/${roomId}`);
  return response.data;
};

// Transcript APIs
export const getFullTranscript = async (trackId) => {
  const response = await apiClient.get(`/api/transcripts/tracks/${trackId}/transcript`);
  return response.data;
};

export const getChunksByTrack = async (trackId, params = {}) => {
  const { limit = 100, skip = 0, sorted_by_index = true } = params;
  const queryParams = new URLSearchParams({
    limit,
    skip,
    sorted_by_index
  });
  
  const response = await apiClient.get(`/api/transcripts/tracks/${trackId}/chunks?${queryParams}`);
  return response.data;
};

export default apiClient;
