import { useState, useEffect } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import { 
  getRoomById, 
  getRoomStatisticsById, 
  getSummaryByRoomId
} from '../services/api';
import { formatDate } from '../utils/datetime';
import { getStatusBadge } from '../utils/display';
import MultilineText from './ui/MultilineText';

const RoomDetail = () => {
  const { roomId } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const fromPage = searchParams.get('from_page') || '0';
  const fromStatus = searchParams.get('status') || '';
  const fromSearch = searchParams.get('search') || '';
  const fromTimePreset = searchParams.get('time_preset') || '';
  const fromUtc = searchParams.get('from_utc') || '';
  const toUtc = searchParams.get('to_utc') || '';
  const hasBackParams = fromPage !== '0' || fromStatus || fromSearch || fromTimePreset;
  const backToListUrl = hasBackParams
    ? `/?${new URLSearchParams({
        ...(fromPage !== '0' && { page: fromPage }),
        ...(fromStatus && { status: fromStatus }),
        ...(fromSearch && { search: fromSearch }),
        ...(fromTimePreset && { time_preset: fromTimePreset }),
        ...(fromUtc && { from_utc: fromUtc }),
        ...(toUtc && { to_utc: toUtc })
      }).toString()}`
    : '/';

  const [room, setRoom] = useState(null);
  const [statistics, setStatistics] = useState(null);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState('overview');

  useEffect(() => {
    fetchRoomData();
  }, [roomId]);

  const fetchRoomData = async () => {
    try {
      setLoading(true);
      setError(null);

      // Fetch room details, statistics, and summaries in parallel
      const [roomData, statsData, summaryData] = await Promise.all([
        getRoomById(roomId),
        getRoomStatisticsById(roomId),
        getSummaryByRoomId(roomId)
      ]);

      setRoom(roomData.room);
      setStatistics(statsData.statistics);
      setSummary(summaryData.data);
    } catch (err) {
      setError(err.message || 'Failed to fetch room data');
      console.error('Error fetching room data:', err);
    } finally {
      setLoading(false);
    }
  };

  // Parse full_text to display with proper formatting
  const parseFullText = (fullText) => {
    if (!fullText) return [];
    const lines = fullText.trim().split('\n');
    
    return lines
      .filter(line => line.trim().length > 0)
      .flatMap(line => {
        // Match pattern: [time] username: content
        const match = line.match(/^\[(.*?)\]\s*([^:]+):\s*(.*)$/);
        
        if (match) {
          const items = [
            // First item: header with timestamp and username
            {
              timestamp: match[1],
              username: match[2].trim(),
              isHeader: true
            }
          ];
          
          // Second item: content (if exists)
          const content = match[3].trim();
          if (content) {
            items.push({
              content: content,
              isContent: true
            });
          }
          
          return items;
        }
        
        // If line doesn't match pattern, return as continuation text
        return [{
          content: line,
          isContinuation: true
        }];
      });
  };

  const formatDuration = (seconds) => {
    if (!seconds) return '0s';
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    
    if (hours > 0) {
      return `${hours}h ${minutes}m ${secs}s`;
    } else if (minutes > 0) {
      return `${minutes}m ${secs}s`;
    }
    return `${secs}s`;
  };

  if (loading) {
    return (
      <div className="flex justify-center items-center h-64">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-lg p-4">
        <p className="text-red-800">Error: {error}</p>
        <div className="mt-4 space-x-2">
          <button 
            onClick={fetchRoomData}
            className="px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700"
          >
            Retry
          </button>
          <button 
            onClick={() => navigate(backToListUrl)}
            className="px-4 py-2 bg-gray-600 text-white rounded hover:bg-gray-700"
          >
            Back to List
          </button>
        </div>
      </div>
    );
  }

  if (!room) {
    return (
      <div className="text-center py-12">
        <p className="text-gray-500">Room not found</p>
        <button 
          onClick={() => navigate(backToListUrl)}
          className="mt-4 px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
        >
          Back to List
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center space-x-4">
          <button
            onClick={() => navigate(backToListUrl)}
            className="text-gray-600 hover:text-gray-900"
          >
            ← Back
          </button>
          <h2 className="text-3xl font-bold text-gray-900">{room.room_name}</h2>
          {getStatusBadge(room.status)}
        </div>
        <button
          onClick={fetchRoomData}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition"
        >
          Refresh
        </button>
      </div>

      {/* Statistics Cards */}
      {statistics && (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div className="bg-white rounded-lg shadow p-6">
            <div className="text-sm font-medium text-gray-500">Total Tracks</div>
            <div className="mt-2 text-3xl font-bold text-gray-900">
              {statistics.total_tracks || 0}
            </div>
          </div>
          <div className="bg-white rounded-lg shadow p-6">
            <div className="text-sm font-medium text-gray-500">Completed</div>
            <div className="mt-2 text-3xl font-bold text-green-600">
              {statistics.completed_tracks || 0}
            </div>
          </div>
          <div className="bg-white rounded-lg shadow p-6">
            <div className="text-sm font-medium text-gray-500">Duration</div>
            <div className="mt-2 text-2xl font-bold text-blue-600">
              {formatDuration(statistics.total_duration_sec)}
            </div>
          </div>
          <div className="bg-white rounded-lg shadow p-6">
            <div className="text-sm font-medium text-gray-500">Segments</div>
            <div className="mt-2 text-3xl font-bold text-purple-600">
              {statistics.total_segments || 0}
            </div>
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="bg-white rounded-lg shadow">
        <div className="border-b border-gray-200">
          <nav className="flex -mb-px">
            <button
              onClick={() => setActiveTab('overview')}
              className={`py-4 px-6 text-sm font-medium ${
                activeTab === 'overview'
                  ? 'border-b-2 border-blue-500 text-blue-600'
                  : 'text-gray-500 hover:text-gray-700 hover:border-gray-300'
              }`}
            >
              Overview
            </button>
            <button
              onClick={() => setActiveTab('participants')}
              className={`py-4 px-6 text-sm font-medium ${
                activeTab === 'participants'
                  ? 'border-b-2 border-blue-500 text-blue-600'
                  : 'text-gray-500 hover:text-gray-700 hover:border-gray-300'
              }`}
            >
              Full Transcript
            </button>
            <button
              onClick={() => setActiveTab('summary')}
              className={`py-4 px-6 text-sm font-medium ${
                activeTab === 'summary'
                  ? 'border-b-2 border-blue-500 text-blue-600'
                  : 'text-gray-500 hover:text-gray-700 hover:border-gray-300'
              }`}
            >
              Summary
            </button>
          </nav>
        </div>

        <div className="p-6">
          {/* Overview Tab */}
          {activeTab === 'overview' && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <div className="text-sm font-medium text-gray-500">Room Name</div>
                  <div className="mt-1 text-gray-900">{room.room_name}</div>
                </div>
                <div>
                  <div className="text-sm font-medium text-gray-500">Status</div>
                  <div className="mt-1">{getStatusBadge(room.status)}</div>
                </div>
                <div>
                  <div className="text-sm font-medium text-gray-500">Created At</div>
                  <div className="mt-1 text-gray-900">{formatDate(room.created_at)}</div>
                </div>
                <div>
                  <div className="text-sm font-medium text-gray-500">Completed At</div>
                  <div className="mt-1 text-gray-900">{formatDate(room.completed_at)}</div>
                </div>
              </div>
            </div>
          )}

          {/* Full Transcript Tab */}
          {activeTab === 'participants' && (
            <div className="space-y-4">
              <h3 className="text-lg font-semibold text-gray-900">Full Transcript</h3>
              {summary && summary.full_text ? (
                <div className="border border-gray-200 rounded-lg p-6">
                  <div className="bg-gray-50 rounded p-4 max-h-[600px] overflow-y-auto">
                    <div className="space-y-1">
                      {parseFullText(summary.full_text).map((item, idx) => (
                        <div key={idx}>
                          {item.isHeader && (
                            // Header with timestamp and username
                            <div className="flex items-center gap-3 px-3 py-2 bg-blue-50 border-l-4 border-blue-500 mt-3">
                              <span className="px-2 py-0.5 bg-blue-600 text-white rounded text-xs font-semibold font-mono">
                                {item.timestamp}
                              </span>
                              <span className="font-bold text-gray-900 text-sm">
                                {item.username}
                              </span>
                            </div>
                          )}
                          
                          {(item.isContent || item.isContinuation) && (
                            // Content - simple style
                            <div className="px-3 py-1 text-gray-700 leading-relaxed">
                              {item.content}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="text-center py-12 bg-gray-50 rounded-lg border border-gray-200">
                  <p className="text-gray-500">No transcript available for this room</p>
                </div>
              )}
            </div>
          )}

          {/* Summary Tab */}
          {activeTab === 'summary' && (
            <div className="space-y-4">
              <h3 className="text-lg font-semibold text-gray-900">Meeting Summary</h3>
              {summary && summary.summary_data ? (
                <div className="space-y-6">
                  <div className="border border-gray-200 rounded-lg p-6">
                    <div className="mb-6">
                      <div className="text-sm text-gray-500 mb-1">Created at</div>
                      <div className="text-gray-900">{formatDate(summary.created_at)}</div>
                    </div>

                    {/* Summary */}
                    {summary.summary_data && (
                      <div className="mb-6">
                        <h4 className="font-semibold text-gray-900 mb-3 text-lg">Summary</h4>
                        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
                          <MultilineText
                            text={summary.summary_data.summary}
                            className="text-gray-800 leading-relaxed"
                          />
                        </div>
                      </div>
                    )}

                    {/* Action Items by Person */}
                    {summary.summary_data.action_items && Object.keys(summary.summary_data.action_items).length > 0 && (
                      <div>
                        <h4 className="font-semibold text-gray-900 mb-3 text-lg">Action Items</h4>
                        <div className="space-y-4">
                          {Object.entries(summary.summary_data.action_items).map(([person, tasks], i) => (
                            <div key={i} className="bg-gray-50 rounded-lg p-4 border border-gray-200">
                              <div className="flex items-center mb-2">
                                <div className="w-10 h-10 bg-blue-500 text-white rounded-full flex items-center justify-center font-semibold text-sm mr-3">
                                  {person.charAt(0).toUpperCase()}
                                </div>
                                <h5 className="font-medium text-gray-900 text-base">{person}</h5>
                              </div>
                              <ul className="ml-13 space-y-1">
                                {tasks.map((task, taskIdx) => (
                                  <li key={taskIdx} className="flex items-start">
                                    <span className="text-blue-500 mr-2 mt-1">•</span>
                                    <span className="text-gray-700">{task}</span>
                                  </li>
                                ))}
                              </ul>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <div className="text-center py-12 bg-gray-50 rounded-lg border border-gray-200">
                  <p className="text-gray-500">No summary available for this room</p>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default RoomDetail;
