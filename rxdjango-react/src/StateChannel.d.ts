export type Listener<T> = (state: T) => void;

export type Model = {
  [key: string]: ModelEntry;
}

export type ModelEntry = {
  [key: string]: string;
}

export interface InstanceType {
  id: number;
  _instance_type: string;
  _operation: string;
  _tstamp: number;
  _deleted?: boolean;
  _loaded: true;
}

export interface UnloadedInstance {
  id: number;
  _loaded: false;
}

export interface AuthStatus {
  status_code: number;
  error?: string | null;
}
