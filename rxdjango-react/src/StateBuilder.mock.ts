import { InstanceType, TempInstance } from './StateChannel.d';

export interface TaskType extends TempInstance {
  taskName: string;
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

export interface TaskPayload extends TempInstance {
  taskName?: string;
}

export interface CustomerPayload extends TempInstance {
  customerName?: string;
  tasks?: number[] | undefined;
}

export interface ProjectPayload extends TempInstance {
  projectName?: string;
  tasks?: number[] | undefined;
  customer?: number;
}

export const ANCHOR = 'project.serializers.ProjectSerializer';

export const MODEL = {
  'project.serializers.ProjectSerializer': {
    'customer': 'project.serializers.CustomerSerializer',
    'tasks': 'project.serializers.TaskSerializer',
  },
  'project.serializers.TaskSerializer': {},
  'project.serializers.CustomerSerializer': {
    'tasks': 'project.serializers.TaskSerializer',
  },
}
