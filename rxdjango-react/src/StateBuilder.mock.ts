import { InstanceType } from './StateChannel.d';

export interface TaskType extends InstanceType {
  name?: string;
}

export interface TaskPayload extends InstanceType {
  name?: string;
}

export interface ProjectType extends InstanceType {
  name?: string;
  tasks?: TaskType[] | undefined;
}

export interface ProjectPayload extends InstanceType {
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
