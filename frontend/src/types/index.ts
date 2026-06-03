export interface Folder {
  id: number;
  name: string;
  color: string;
  created_at: string;
  doc_count: number;
  chunk_count: number;
}

export interface Document {
  id: number;
  folder_id: number;
  filename: string;
  file_size: number;
  status: string;
  uploaded_at: string;
  indexed_at: string | null;
  error_msg: string | null;
  doc_metadata: Record<string, any>;
  chunk_count: number;
}

export interface Sheet {
  id: number;
  name: string;
  sheet_index: number;
  sheet_type: string;
  classifier_confidence: number;
}

export interface Citation {
  index: number;
  chunk_id: number;
  filename: string;
  sheet_name: string;
  sheet_type: string;
  hierarchy_path: string;
  score: number;
  sources: string[];
  metadata: Record<string, any>;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  citations?: Citation[];
  confidence?: number | null;
  streaming?: boolean;
  context_label?: string;
}

export interface ProgressEvent {
  stage: string;
  message: string;
  current: number;
  total: number;
  extra?: Record<string, any>;
}

export type ContextMode =
  | { kind: 'all' }
  | { kind: 'folder'; id: number }
  | { kind: 'document'; id: number };
