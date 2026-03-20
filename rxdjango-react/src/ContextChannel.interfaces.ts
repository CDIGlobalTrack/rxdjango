export type Listener<T> = (state: T) => void;
export type InstanceListener = (instance: InstanceType) => void;
export type NoConnectionListener = (no_connection_since: Date | undefined) => void;

export type Saveable<T extends InstanceType, P = Partial<T>> = T & {
  save(data: P): Promise<void>;
};

export type Deleteable<T extends InstanceType> = T & {
  delete(): Promise<void>;
};

export type Creatable<T extends InstanceType, P = Partial<T>> = T[] & {
  create(data?: P): Promise<number>;
};

// Writable declaration: maps instance types to allowed operations
export type Writable = {
  [key: string]: readonly ('save' | 'create' | 'delete')[];
}

// Write callbacks passed to StateBuilder for attaching write methods to instances/arrays
export type WriteCallbacks = {
  saveInstance: (instanceType: string, instanceId: number, data: Record<string, unknown>) => Promise<void>;
  createInstance: (instanceType: string, parentType: string, parentId: number, relationName: string, data?: Record<string, unknown>) => Promise<number>;
  deleteInstance: (instanceType: string, instanceId: number) => Promise<void>;
}

// The model for a channel contains which fields for each instance type are references
// to other types.
// Key is the _instance_type field (something like myapp.serializer.MyModelSerializer)
export type Model = {
  [key: string]: ModelEntry;
}

// key is a property name, value is the instance type the property relates to
export type ModelEntry = {
  [key: string]: string;
}

// This represents one instance in the backend, serialized by a serializer.
export interface InstanceType {
  // The instance id in database
  id: number;
  // Instance type is the path of serializer used to serialize it
  _instance_type: string;
  // The latest operation: created, updated, deleted
  _operation: string;
  // The timestamp of latest update
  _tstamp: number;
  // If instance has been deleted already
  _deleted?: boolean;
  // _loaded is false is instance has been referenced by another
  // it's set to true once it's data is received from server
  _loaded?: boolean;
}

// This is a raw instance data as it comes from server
// It's then converted to an InstanceType
export interface TempInstance extends InstanceType {
  [index: string]: string | number | object | Date | null | undefined | boolean;
}

// This is an address of a reference to an instance inside another reference
export interface InstanceReference {
  // The _instance_type:id key of the referring instance
  referrerKey: string;
  // The property in the referring instance that points to the referred instance
  property: string;
}

// An instance type that has not been loaded yet
export interface UnloadedInstance extends InstanceType {
  id: number;
  _loaded: false;
}

export interface AuthStatus {
  type: 'auth';
  statusCode: number;
  error?: string | null;
}

export interface SystemMessage {
  type: 'system' | 'maintenance';
  source: string;
  message: string;
}
