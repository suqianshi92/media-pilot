import type {
  AgentStatusSummary,
  FileAssetDto,
  MetadataDetailDto,
  OperationRecordDto,
  ProviderCallDto,
  SearchKeywordDto,
  TaskDetailDto,
  TaskStatusSummary,
  TaskSummary,
  TimelineEventDto,
  MetadataCandidateDto,
  WritePlanDto,
  WriteResultDto,
} from '@/types/task'

function createStatusSummary(
  overrides: Partial<TaskStatusSummary>,
): TaskStatusSummary {
  return {
    status: 'discovered',
    current_step: 'download_scan',
    failure_reason: null,
    confidence: null,
    confidence_level: 'unknown',
    latest_message: null,
    ...overrides,
  }
}

function createTaskSummary(overrides: Partial<TaskSummary> & Pick<TaskSummary, 'id' | 'source_path'>): TaskSummary {
  const { id, source_path, ...rest } = overrides

  const rawStatus = overrides.status_summary?.status ?? 'discovered'
  const computedTotalStatus = rawStatus

  return {
    id,
    source_path,
    title: null,
    year: null,
    media_type: 'unknown',
    can_confirm: false,
    flow_type: 'external_import',
    total_status: computedTotalStatus,
    metadata_status: 'unknown',
    file_format: null,
    download_task: null,
    created_at: '2026-05-08T10:00:00+08:00',
    updated_at: '2026-05-08T10:00:00+08:00',
    status_summary: createStatusSummary({}),
    agent_status_summary: null,
    ...rest,
  }
}

function createSearchKeyword(overrides: Partial<SearchKeywordDto> & Pick<SearchKeywordDto, 'keyword'>): SearchKeywordDto {
  const { keyword, ...rest } = overrides
  return {
    keyword,
    source: 'rule',
    confidence: 0.95,
    reason: 'filename_rule_cleanup',
    rule_keyword: keyword,
    explanation: null,
    quality_tokens: [],
    tokens_removed: [],
    ...rest,
  }
}

function createCandidate(overrides: Partial<MetadataCandidateDto> & Pick<MetadataCandidateDto, 'provider_id' | 'title'>): MetadataCandidateDto {
  const { provider_id, title, ...rest } = overrides
  return {
    provider: 'tmdb',
    provider_id,
    title,
    original_title: null,
    year: null,
    media_type: 'movie',
    overview: null,
    poster_url: null,
    confidence: 0.9,
    match_reason: 'title_exact',
    risk_flags: [],
    payload: {},
    ...rest,
  }
}

// 注: 旧 createConfirmationRequest 已随 ConfirmationRequestDto 一起下线。

function createTimelineEvent(
  key: string,
  title: string,
  created_at: string,
  detail: string | null = null,
  tone: TimelineEventDto['tone'] = 'default',
): TimelineEventDto {
  return { key, title, created_at, detail, tone }
}

function createTaskDetail(overrides: Partial<TaskDetailDto> & Pick<TaskDetailDto, 'task'>): TaskDetailDto {
  const { task, ...rest } = overrides
  return {
    task,
    source_selection: null,
    search_keyword: null,
    selected_candidate: null,
    metadata_detail: null,
    write_plan: null,
    write_result: null,
    file_assets: [],
    provider_calls: [],
    operation_records: [],
    audit_logs: [],
    timeline: [],
    episode_mappings: [],
    ...rest,
  }
}


import type { DownloadTaskSummary } from '@/types/task'

function createDownloadTask(overrides: Partial<DownloadTaskSummary> & Pick<DownloadTaskSummary, 'id' | 'title' | 'save_path'>): DownloadTaskSummary {
  const { id, title, save_path, ...rest } = overrides
  return {
    id,
    title,
    source: 'prowlarr',
    save_path,
    qb_hash: null,
    content_path: null,
    progress: 0,
    download_speed_bytes_per_second: null,
    upload_speed_bytes_per_second: null,
    seeders: 0,
    leechers: 0,
    connections: null,
    qb_state: null,
    status: 'submitted',
    error_message: null,
    ingest_task_id: null,
    created_at: '2026-05-08T10:00:00+08:00',
    updated_at: '2026-05-08T10:00:00+08:00',
    ...rest,
  }
}

export const mockDownloadTasks: DownloadTaskSummary[] = [
  createDownloadTask({
    id: 'dl-downloading',
    title: '天气之子 2019 1080p BluRay x264',
    save_path: '/data/downloads',
    qb_hash: 'abc123',
    content_path: '/data/downloads/天气之子.2019.1080p.mkv',
    progress: 0.72,
    download_speed_bytes_per_second: 5242880,
    upload_speed_bytes_per_second: 102400,
    seeders: 42,
    leechers: 3,
    connections: 22,
    qb_state: 'downloading',
    status: 'downloading',
  }),
  createDownloadTask({
    id: 'dl-submitted',
    title: '铃芽之旅 2022 1080p WEB-DL',
    save_path: '/data/downloads',
    qb_hash: 'def456',
    status: 'submitted',
  }),
  createDownloadTask({
    id: 'dl-completed',
    title: '秒速五厘米 2007 1080p BluRay',
    save_path: '/data/downloads',
    qb_hash: 'ghi789',
    content_path: '/data/downloads/秒速五厘米.2007.1080p.mkv',
    progress: 1.0,
    seeders: 15,
    qb_state: 'uploading',
    status: 'completed',
    ingest_task_id: 'task-completed',
  }),
  createDownloadTask({
    id: 'dl-failed',
    title: '龙猫 1988 1080p WEB-DL',
    save_path: '/data/downloads',
    qb_hash: 'jkl012',
    status: 'sync_failed',
    error_message: 'qBittorrent API 调用失败',
    updated_at: '2026-05-22T02:45:44.580666',
  }),
]

const weatherCandidate = createCandidate({
  provider_id: 'movie:568160',
  title: '天气之子',
  original_title: '天気の子',
  year: 2019,
  overview: '离家少年与拥有晴天能力的少女相遇。',
  poster_url: 'https://image.tmdb.org/t/p/w342/weathering-with-you.jpg',
  confidence: 0.96,
  match_reason: 'title_exact,rank_1',
})

// 注: 旧 mock 中用于 ConfirmationSection 的 documentary / unrelated 候选已下线。
// ConfirmationRequest 通道下线后, 这些候选失去消费者, 移除以避免 lint 噪音。

const completedWritePlan: WritePlanDto = {
  target_dir: '/data/library/movies/天气之子 (2019)',
  target_file: '/data/library/movies/天气之子 (2019)/天气之子 (2019).mkv',
  nfo_path: '/data/library/movies/天气之子 (2019)/天气之子 (2019).nfo',
  poster_path: '/data/library/movies/天气之子 (2019)/天气之子 (2019)-poster.jpg',
  fanart_path: '/data/library/movies/天气之子 (2019)/天气之子 (2019)-fanart.jpg',
  clearlogo_path: '/data/library/movies/天气之子 (2019)/天气之子 (2019)-clearlogo.png',
  conflict_status: null,
  conflict_reason: null,
}

const completedWriteResult: WriteResultDto = {
  status: 'succeeded',
  failure_reason: null,
  warnings: [],
  written_paths: [
    completedWritePlan.target_file!,
    completedWritePlan.nfo_path!,
    completedWritePlan.poster_path!,
  ],
}

const completedMetadata: MetadataDetailDto = {
  provider: 'tmdb',
  provider_id: 'movie:568160',
  media_type: 'movie',
  title: '天气之子',
  original_title: '天気の子',
  year: 2019,
  overview: '离家少年与拥有晴天能力的少女相遇。',
  release_date: '2019-07-19',
  runtime_minutes: 112,
  rating: 7.9,
  tmdb_id: '568160',
  imdb_id: 'tt9426210',
  genres: ['动画', '爱情', '奇幻'],
  countries: ['日本'],
  studios: ['CoMix Wave Films'],
  directors: [
    {
      provider_id: '1134560',
      name: '新海诚',
      role: 'Director',
      profile_url: 'https://www.themoviedb.org/person/1134560',
      image_url: null,
    },
  ],
  actors: [
    {
      provider_id: '2260180',
      name: '醍醐虎汰朗',
      role: '森岛帆高',
      profile_url: 'https://www.themoviedb.org/person/2260180',
      image_url: null,
    },
  ],
  poster_url: '/api/v1/tasks/task-completed/assets/poster',
  fanart_url: '/api/v1/tasks/task-completed/assets/fanart',
  clearlogo_url: '/api/v1/tasks/task-completed/assets/clearlogo',
}

const commonFileAssets: FileAssetDto[] = [
  {
    role: 'source_file',
    path: '/data/downloads/天气之子.mkv',
    size_bytes: 52004693366,
  },
]

const commonProviderCalls: ProviderCallDto[] = [
  {
    adapter_name: 'tmdb',
    action: 'search_movie',
    status: 'succeeded',
    error_message: null,
    created_at: '2026-05-08T10:03:00+08:00',
  },
]

const commonOperations: OperationRecordDto[] = [
  {
    operation_type: 'prepare_workspace',
    permission_level: 'safe_write',
    source_path: '/data/downloads/天气之子.mkv',
    target_path: '/data/workspace/task-multiple-candidates',
    status: 'succeeded',
    details: {},
    created_at: '2026-05-08T10:02:00+08:00',
  },
]

export const mockTaskDetails: TaskDetailDto[] = [
  createTaskDetail({
    task: createTaskSummary({
      id: 'task-multiple-candidates',
      source_path: '/data/downloads/天气之子.mkv',
      title: '天气之子',
      year: 2019,
      media_type: 'movie',
      can_confirm: true,
      agent_status_summary: {
        run_status: 'waiting_user',
        latest_run_id: 'run-mock-1',
        pending_decision_count: 1,
        latest_message_summary: '请选择要使用的元数据候选',
      } satisfies AgentStatusSummary,
      status_summary: createStatusSummary({
        status: 'waiting_user',
        current_step: 'metadata_detail',
        confidence: 0.95,
        confidence_level: 'high',
        latest_message: 'blocked:multiple_metadata_candidates',
      }),
    }),
    source_selection: {
      input_path: '/data/workspace/天气之子.mkv',
      selected_path: '/data/workspace/天气之子.mkv',
      confidence: 1,
      reason: 'single_video_file',
      bdmv_detected: false,
      stream_file_count: null,
      candidate_files: [],
      excluded_files: [],
    },
    search_keyword: createSearchKeyword({ keyword: '天气之子' }),
    file_assets: commonFileAssets,
    provider_calls: commonProviderCalls,
    operation_records: commonOperations,
    timeline: [
      createTimelineEvent('scan', '已扫描下载目录', '2026-05-08T10:00:00+08:00'),
      createTimelineEvent('search', '已完成 TMDB 搜索', '2026-05-08T10:03:00+08:00'),
    ],
  }),
  createTaskDetail({
    task: createTaskSummary({
      id: 'task-processing',
      source_path: '/data/downloads/铃芽之旅.mkv',
      title: '铃芽之旅',
      year: 2022,
      media_type: 'movie',
      agent_status_summary: {
        run_status: 'active',
        latest_run_id: 'run-mock-2',
        pending_decision_count: 0,
        latest_message_summary: '正在写入 NFO 和媒体资源...',
      } satisfies AgentStatusSummary,
      status_summary: createStatusSummary({
        status: 'processing',
        current_step: 'write_metadata_assets',
        confidence: 0.93,
        confidence_level: 'high',
        latest_message: 'step:write_metadata_assets',
      }),
    }),
    selected_candidate: createCandidate({
      provider_id: 'movie:916224',
      title: '铃芽之旅',
      year: 2022,
    }),
    search_keyword: createSearchKeyword({ keyword: '铃芽之旅 2022' }),
    metadata_detail: {
      ...completedMetadata,
      provider_id: 'movie:916224',
      tmdb_id: '916224',
      title: '铃芽之旅',
      original_title: 'すずめの戸締まり',
      year: 2022,
    },
    timeline: [
      createTimelineEvent('queued', '已进入后台处理', '2026-05-08T10:08:00+08:00'),
      createTimelineEvent('processing', '正在写入媒体库', '2026-05-08T10:09:00+08:00'),
    ],
  }),
  createTaskDetail({
    task: createTaskSummary({
      id: 'task-completed',
      source_path: '/data/downloads/天气之子.mkv',
      title: '天气之子',
      year: 2019,
      media_type: 'movie',
      flow_type: 'managed_download',
      download_task: mockDownloadTasks[2],  // dl-completed
      agent_status_summary: {
        run_status: 'completed',
        latest_run_id: 'run-mock-3',
        pending_decision_count: 0,
        latest_message_summary: '元数据确认完成，已写入媒体库',
      } satisfies AgentStatusSummary,
      status_summary: createStatusSummary({
        status: 'library_import_complete',
        current_step: 'library_import_complete',
        confidence: 0.96,
        confidence_level: 'high',
        latest_message: 'step:library_import_complete',
      }),
    }),
    selected_candidate: weatherCandidate,
    search_keyword: createSearchKeyword({ keyword: '天气之子 2019', source: 'manual' }),
    metadata_detail: completedMetadata,
    write_plan: completedWritePlan,
    write_result: completedWriteResult,
    file_assets: [
      {
        role: 'library_video',
        path: completedWritePlan.target_file!,
        size_bytes: 52004693366,
      },
      {
        role: 'library_nfo',
        path: completedWritePlan.nfo_path!,
        size_bytes: 2048,
      },
      {
        role: 'library_poster',
        path: completedWritePlan.poster_path!,
        size_bytes: 512000,
      },
      {
        role: 'library_fanart',
        path: completedWritePlan.fanart_path!,
        size_bytes: 2048000,
      },
      {
        role: 'library_clearlogo',
        path: completedWritePlan.clearlogo_path!,
        size_bytes: 102400,
      },
    ],
    provider_calls: [
      ...commonProviderCalls,
      {
        adapter_name: 'tmdb',
        action: 'get_movie_details',
        status: 'succeeded',
        error_message: null,
        created_at: '2026-05-08T10:04:00+08:00',
      },
    ],
    operation_records: [
      ...commonOperations,
      {
        operation_type: 'copy_to_staging',
        permission_level: 'safe_write',
        source_path: '/data/downloads/天气之子.mkv',
        target_path: '/data/library/movies/.media-pilot-staging/task-completed/天气之子 (2019)/天气之子 (2019).mkv',
        status: 'succeeded',
        details: {
          transfer_method: 'copy',
          duration_ms: 18234,
        },
        created_at: '2026-05-08T10:06:00+08:00',
      },
      {
        operation_type: 'publish_to_library',
        permission_level: 'safe_write',
        source_path: '/data/library/movies/.media-pilot-staging/task-completed/天气之子 (2019)',
        target_path: '/data/library/movies/天气之子 (2019)',
        status: 'succeeded',
        details: {},
        created_at: '2026-05-08T10:06:20+08:00',
      },
    ],
    audit_logs: [
      {
        actor: 'system',
        action: 'file_operation_recorded',
        object_type: 'operation_record',
        object_id: 'copy-to-staging-1',
        created_at: '2026-05-08T10:06:01+08:00',
        context: {
          operation_type: 'copy_to_staging',
          source_path: '/data/downloads/天气之子.mkv',
          target_path: '/data/library/movies/.media-pilot-staging/task-completed/天气之子 (2019)/天气之子 (2019).mkv',
          status: 'succeeded',
        },
      },
    ],
    timeline: [
      createTimelineEvent('download_requested', '提交下载', '2026-05-08T09:50:00+08:00', '来源: prowlarr / 天气之子 2019 1080p BluRay x264'),
      createTimelineEvent('download_completed', '下载完成', '2026-05-08T10:00:00+08:00', '文件: 天气之子.mkv', 'success'),
      createTimelineEvent('confirmed', '已确认候选', '2026-05-08T10:03:30+08:00'),
      createTimelineEvent('completed', '已完成媒体入库', '2026-05-08T10:06:30+08:00', null, 'success'),
    ],
  }),
  createTaskDetail({
    task: createTaskSummary({
      id: 'task-completed-no-images',
      source_path: '/data/downloads/天气之子.mkv',
      title: '天气之子',
      year: 2019,
      media_type: 'movie',
      status_summary: createStatusSummary({
        status: 'library_import_complete',
        current_step: 'library_import_complete',
        confidence: 0.96,
        confidence_level: 'high',
        latest_message: 'step:library_import_complete',
      }),
    }),
    selected_candidate: weatherCandidate,
    search_keyword: createSearchKeyword({ keyword: '天气之子 2019', source: 'manual' }),
    metadata_detail: {
      ...completedMetadata,
      poster_url: null,
      fanart_url: null,
      clearlogo_url: null,
    },
    write_plan: completedWritePlan,
    write_result: completedWriteResult,
    file_assets: [
      {
        role: 'library_video',
        path: completedWritePlan.target_file!,
        size_bytes: 52004693366,
      },
    ],
    provider_calls: commonProviderCalls,
    operation_records: [...commonOperations],
    timeline: [
      createTimelineEvent('scan', '已扫描下载目录', '2026-05-08T10:00:00+08:00'),
      createTimelineEvent('import', '已完成媒体入库', '2026-05-08T10:06:30+08:00', null, 'success'),
    ],
  }),
  createTaskDetail({
    task: createTaskSummary({
      id: 'task-failed-rollback',
      source_path: '/data/downloads/秒速五厘米.mkv',
      title: '秒速五厘米',
      year: 2007,
      media_type: 'movie',
      agent_status_summary: {
        run_status: 'failed',
        latest_run_id: 'run-mock-4',
        pending_decision_count: 0,
        latest_message_summary: '海报下载失败: HTTP 404',
      } satisfies AgentStatusSummary,
      status_summary: createStatusSummary({
        status: 'agent_failed',
        current_step: 'write_metadata_assets',
        failure_reason: 'poster_download_failed',
        confidence: 0.88,
        confidence_level: 'medium',
        latest_message: 'blocked:missing_poster',
      }),
    }),
    selected_candidate: createCandidate({
      provider_id: 'movie:38142',
      title: '秒速五厘米',
      year: 2007,
    }),
    write_plan: {
      ...completedWritePlan,
      target_dir: '/data/library/movies/秒速五厘米 (2007)',
      target_file: '/data/library/movies/秒速五厘米 (2007)/秒速五厘米 (2007).mkv',
      nfo_path: '/data/library/movies/秒速五厘米 (2007)/秒速五厘米 (2007).nfo',
      poster_path: '/data/library/movies/秒速五厘米 (2007)/秒速五厘米 (2007)-poster.jpg',
      fanart_path: '/data/library/movies/秒速五厘米 (2007)/秒速五厘米 (2007)-fanart.jpg',
      clearlogo_path: '/data/library/movies/秒速五厘米 (2007)/秒速五厘米 (2007)-clearlogo.png',
    },
    write_result: {
      status: 'failed',
      failure_reason: 'poster_download_failed',
      warnings: [],
      written_paths: ['/data/library/movies/秒速五厘米 (2007)/秒速五厘米 (2007).nfo'],
    },
    timeline: [
      createTimelineEvent('failed', '写入过程中断', '2026-05-08T10:12:00+08:00', '海报下载失败', 'error'),
    ],
  }),
  createTaskDetail({
    task: createTaskSummary({
      id: 'task-bdmv-manual',
      source_path: '/data/downloads/你的名字/BDMV',
      title: '你的名字。',
      year: 2016,
      media_type: 'movie',
      can_confirm: false,
      file_format: 'BDMV',
      metadata_status: 'complete',
      status_summary: createStatusSummary({
        status: 'library_import_complete',
        current_step: 'library_import_complete',
        confidence: 1,
        confidence_level: 'high',
        latest_message: '已按 BDMV 电影目录完成入库',
      }),
    }),
    source_selection: {
      input_path: '/data/workspace/你的名字/BDMV',
      selected_path: null,
      confidence: 1,
      reason: 'auto_bdmv_movie_dir',
      bdmv_detected: true,
      stream_file_count: 7,
      candidate_files: [],
      excluded_files: [],
    },
    write_plan: {
      ...completedWritePlan,
      target_dir: '/data/library/movies/你的名字。 (2016)',
      target_file: '/data/library/movies/你的名字。 (2016)/BDMV/index.bdmv',
      nfo_path: '/data/library/movies/你的名字。 (2016)/BDMV/index.nfo',
      poster_path: '/data/library/movies/你的名字。 (2016)/你的名字。 (2016)-poster.jpg',
      fanart_path: '/data/library/movies/你的名字。 (2016)/你的名字。 (2016)-fanart.jpg',
      clearlogo_path: '/data/library/movies/你的名字。 (2016)/你的名字。 (2016)-clearlogo.png',
    },
    write_result: {
      ...completedWriteResult,
      written_paths: [
        '/data/library/movies/你的名字。 (2016)/BDMV/index.bdmv',
        '/data/library/movies/你的名字。 (2016)/BDMV/index.nfo',
        '/data/library/movies/你的名字。 (2016)/你的名字。 (2016)-poster.jpg',
      ],
    },
  }),
  createTaskDetail({
    task: createTaskSummary({
      id: 'task-no-candidates',
      source_path: '/data/downloads/未知标题.mkv',
      title: '未知标题',
      media_type: 'movie',
      can_confirm: true,
      status_summary: createStatusSummary({
        status: 'waiting_user',
        current_step: 'metadata_detail',
        confidence: 0.42,
        confidence_level: 'low',
        latest_message: 'blocked:no_metadata_candidates',
      }),
    }),
    search_keyword: createSearchKeyword({
      keyword: '未知标题',
      confidence: 0.48,
      reason: 'rule_keyword_low_confidence',
    }),
  }),
  createTaskDetail({
    task: createTaskSummary({
      id: 'task-provider-failed',
      source_path: '/data/downloads/故障样本.mkv',
      title: '故障样本',
      media_type: 'movie',
      can_confirm: true,
      status_summary: createStatusSummary({
        status: 'waiting_user',
        current_step: 'metadata_detail',
        confidence: 0.63,
        confidence_level: 'medium',
        latest_message: 'blocked:metadata_provider_failed',
      }),
    }),
    search_keyword: createSearchKeyword({ keyword: '故障样本 2024' }),
  }),
  createTaskDetail({
    task: createTaskSummary({
      id: 'task-low-confidence',
      source_path: '/data/downloads/天气之子-低置信度.mkv',
      title: '天气之子',
      media_type: 'movie',
      can_confirm: true,
      status_summary: createStatusSummary({
        status: 'waiting_user',
        current_step: 'metadata_detail',
        confidence: 0.61,
        confidence_level: 'low',
        latest_message: 'blocked:low_ai_confidence',
      }),
    }),
    search_keyword: createSearchKeyword({
      keyword: '天气之子 2019',
      confidence: 0.62,
      source: 'llm',
      explanation: '从文件名补全年份以提高命中率',
    }),
  }),
]
