import { InstanceType, TempInstance } from './StateChannel.d';

export interface TaskType extends TempInstance {
  name?: string;
}

export interface TaskPayload extends TempInstance {
  name?: string;
}

export interface ProjectType extends TempInstance {
  name?: string;
  tasks?: TaskType[] | undefined;
}

export interface ProjectPayload extends TempInstance {
  name?: string;
  tasks?: number[] | undefined;
}

export const ANCHOR = 'project.serializers.ProjectSerializer';

export const MODEL = {
  'project.serializers.ProjectSerializer': {
    'tasks': 'project.serializers.TaskSerializer',
  },
  'project.serializers.TaskSerializer': {},
}
