import { useState, useEffect } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import {
  getRoomStatisticsById,
  getSummaryByRoomId,
  getRoomAudioInfoById,
  buildAudioUrl
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

  const [statistics, setStatistics] = useState(null);
  const [summary, setSummary] = useState(null);
  const [audioFiles, setAudioFiles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [audioLoading, setAudioLoading] = useState(false);
  const [audioError, setAudioError] = useState(null);
  const [activeTab, setActiveTab] = useState('overview');

  useEffect(() => {
    fetchRoomData();
  }, [roomId]);

  const fetchRoomData = async () => {
    try {
      setLoading(true);
      setError(null);
      setAudioError(null);

      // Fetch room details, statistics, and summaries in parallel
      const [statsData, summaryData] = await Promise.all([
        getRoomStatisticsById(roomId),
        getSummaryByRoomId(roomId)
      ]);

      setStatistics(statsData.statistics);
      setSummary(summaryData.data);

      setAudioLoading(true);
      try {
        const audioData = await getRoomAudioInfoById(roomId);
        setAudioFiles(audioData.file_results || []);
      setAudioError(null);
      } catch (audioErr) {
        setAudioFiles([]);
        setAudioError(audioErr.message || 'Failed to fetch audio files');
      }
    } catch (err) {
      setError(err.message || 'Failed to fetch room data');
      console.error('Error fetching room data:', err);
    } finally {
      setAudioLoading(false);
      setLoading(false);
    }
  };

  const formatAudioDuration = (startedAtNs, endedAtNs) => {
    if (!startedAtNs || !endedAtNs) {
      return null;
    }

    const durationSeconds = Math.max(0, Number(endedAtNs) - Number(startedAtNs)) / 1e9;
    if (!Number.isFinite(durationSeconds)) {
      return null;
    }

    return formatDuration(durationSeconds);
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

  if (!statistics) {
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
          <h2 className="text-3xl font-bold text-gray-900">{statistics.room_id}</h2>
          {getStatusBadge(statistics.status)}
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
              className={`py-4 px-6 text-sm font-medium ${activeTab === 'overview'
                ? 'border-b-2 border-blue-500 text-blue-600'
                : 'text-gray-500 hover:text-gray-700 hover:border-gray-300'
                }`}
            >
              Overview
            </button>
            <button
              onClick={() => setActiveTab('participants')}
              className={`py-4 px-6 text-sm font-medium ${activeTab === 'participants'
                ? 'border-b-2 border-blue-500 text-blue-600'
                : 'text-gray-500 hover:text-gray-700 hover:border-gray-300'
                }`}
            >
              Full Transcript
            </button>
            <button
              onClick={() => setActiveTab('summary')}
              className={`py-4 px-6 text-sm font-medium ${activeTab === 'summary'
                ? 'border-b-2 border-blue-500 text-blue-600'
                : 'text-gray-500 hover:text-gray-700 hover:border-gray-300'
                }`}
            >
              Summary
            </button>
            <button
              onClick={() => setActiveTab('audio')}
              className={`py-4 px-6 text-sm font-medium ${activeTab === 'audio'
                ? 'border-b-2 border-blue-500 text-blue-600'
                : 'text-gray-500 hover:text-gray-700 hover:border-gray-300'
                }`}
            >
              Audio
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
                  <div className="mt-1 text-gray-900">{statistics.room_name}</div>
                </div>
                <div>
                  <div className="text-sm font-medium text-gray-500">Status</div>
                  <div className="mt-1">{getStatusBadge(statistics.status)}</div>
                </div>
                <div>
                  <div className="text-sm font-medium text-gray-500">Created At</div>
                  <div className="mt-1 text-gray-900">{formatDate(statistics.created_at)}</div>
                </div>
                <div>
                  <div className="text-sm font-medium text-gray-500">Completed At</div>
                  <div className="mt-1 text-gray-900">{formatDate(statistics.completed_at)}</div>
                </div>
              </div>
            </div>
          )}

          {/* Full Transcript Tab */}
          {activeTab === 'participants' && (
            <div className="space-y-4">
              <h3 className="text-lg font-semibold text-gray-900">Full Transcript</h3>
              {summary && summary.messages && summary.messages.length > 0 ? (
                <div className="border border-gray-200 rounded-lg p-6">
                  <div className="bg-gray-50 rounded p-4 max-h-[600px] overflow-y-auto">
                    <div className="space-y-1">
                      {summary.messages.map((item, idx) => (
                        <div key={idx}>
                          {/* Header with timestamp and participant_id */}
                          <div className="flex items-center gap-3 px-3 py-2 bg-blue-50 border-l-4 border-blue-500 mt-3">
                            <span className="px-2 py-0.5 bg-blue-600 text-white rounded text-xs font-semibold font-mono">
                              {item.timestamp}
                            </span>
                            <span className="font-bold text-gray-900 text-sm">
                              {item.participant_id}
                            </span>
                          </div>

                          {/* Content - simple style */}
                          <div className="px-3 py-1 text-gray-700 leading-relaxed whitespace-pre-wrap">
                            {item.content}
                          </div>
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

          {/* Audio Tab */}
          {activeTab === 'audio' && (
            <div className="space-y-4">
              <h3 className="text-lg font-semibold text-gray-900">Room Audio</h3>
              {audioLoading ? (
                <div className="flex items-center justify-center py-12 text-gray-500">
                  Loading audio files...
                </div>
              ) : audioError ? (
                <div className="text-center py-12 bg-red-50 rounded-lg border border-red-200">
                  <p className="text-red-700">{audioError}</p>
                </div>
              ) : audioFiles.length > 0 ? (
                <div className="space-y-4">
                  {audioFiles.map((audioFile, index) => {
                    const audioUrl = buildAudioUrl(audioFile.filename);
                    const durationLabel = formatAudioDuration(audioFile.started_at_ns, audioFile.ended_at_ns);

                    return (
                      <div key={`${audioFile.filename}-${index}`} className="border border-gray-200 rounded-lg p-4 bg-gray-50">
                        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
                          <div>
                            <div className="text-sm text-gray-500">Participant</div>
                            <div className="font-medium text-gray-900">{audioFile.participant_identity || 'Unknown'}</div>
                          </div>
                          <div className="text-sm text-gray-500 break-all md:text-right">
                            {audioFile.filename}
                          </div>
                        </div>

                        <div className="mt-4">
                          <audio controls className="w-full" src={audioUrl}>
                            Your browser does not support the audio element.
                          </audio>
                        </div>

                        <div className="mt-3 flex flex-wrap items-center gap-4 text-sm text-gray-600">
                          {durationLabel && <span>Duration: {durationLabel}</span>}
                          <a
                            href={audioUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="text-blue-600 hover:text-blue-800"
                          >
                            Open audio file
                          </a>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="text-center py-12 bg-gray-50 rounded-lg border border-gray-200">
                  <p className="text-gray-500">No audio files available for this room</p>
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
