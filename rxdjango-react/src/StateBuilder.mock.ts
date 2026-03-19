import { TempInstance } from './ContextChannel.interfaces';

export interface UserType extends TempInstance {
  username: string;
}

export interface TaskType extends TempInstance {
  taskName: string;
  user?: UserType;
}

export interface CustomerType extends TempInstance {
  customerName: string;
  tasks: TaskType[];
}

export interface ProjectType extends TempInstance {
  projectName: string;
  tasks: TaskType[];
  customer: CustomerType;
}

// Write payload types — only writable fields, used by Saveable/Creatable
export type TaskPayload = {
  taskName?: string;
  user?: number;
};

export type CustomerPayload = {
  customerName?: string;
};

export type ProjectPayload = {
  projectName?: string;
};

// Test data types — extend TempInstance with relation IDs for StateBuilder tests
export interface ProjectTestData extends TempInstance {
  projectName?: string;
  tasks?: number[];
  customer?: number;
}

export interface CustomerTestData extends TempInstance {
  customerName?: string;
  tasks?: number[];
}

export interface TaskTestData extends TempInstance {
  taskName?: string;
  user?: number;
}

export const ANCHOR = 'project.serializers.ProjectSerializer';

export const MODEL = {
  'project.serializers.ProjectSerializer': {
    'customer': 'project.serializers.CustomerSerializer',
    'tasks': 'project.serializers.TaskSerializer',
  },
  'project.serializers.TaskSerializer': {
    'user': 'project.serializers.UserSerializer',
  },
  'project.serializers.CustomerSerializer': {
    'tasks': 'project.serializers.TaskSerializer',
  },
  'project.serializers.UserSerializer': {},
}
