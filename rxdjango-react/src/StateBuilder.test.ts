import InstanceHandler from './InstanceHandler';
import StateBuilder from './StateBuilder';
import { InstanceType, UnloadedInstance, Model } from './StateChannel.d';
import {
  ProjectType,
  ANCHOR,
  MODEL,
} from './StateBuilder.mock'

const header = (
  instanceType: string,
  id: number,
  operation: string = 'create',
  loaded = true,
): InstanceType | UnloadedInstance => {
  return {
    id: id,
    '_instance_type': `project.serializers.${instanceType}`,
    '_operation': operation,
    '_tstamp': new Date().getTime(),
    '_loaded': loaded,
  };
};

describe('StateBuilder', () => {
  let stateBuilder: StateBuilder<ProjectType>;

  beforeEach(() => {
    stateBuilder = new StateBuilder<ProjectType>(MODEL, ANCHOR);
  });

  it('should be created with the correct initial properties', () => {
    expect(stateBuilder).toBeDefined();
    expect(stateBuilder.state).toBeUndefined();
  });

  it('anchor is received', () => {
    const instances = [
      {
        ...header('ProjectSerializer', 1),
        name: 'Test project',
      } as ProjectType,
    ];
    stateBuilder.update(instances);
    expect(stateBuilder.state!.id).toBe(1);
    expect(stateBuilder.state!.name).toBe('Test project');
  });

  it('empty list of related objects is preserved', () => {
    const instances = [
      {
        ...header('ProjectSerializer', 1),
        name: 'Test project',
        tasks: [],
      } as ProjectType,
    ];
    stateBuilder.update(instances);
    expect(stateBuilder.state!.id).toBe(1);
    expect(stateBuilder.state!.name).toBe('Test project');
    expect(stateBuilder.state!.tasks).toEqual([]);
  });

  it('related objects are initialized as null', () => {
    const instances: = [
      {
        ...header('ProjectSerializer', 1),
        name: 'Test project',
        tasks: [1, 2, 3],
      } as unknown as ProjectType,
    ];
    stateBuilder.update(instances as ProjectType[]);
    // null means the object has already been requested
    expect(stateBuilder.state!.tasks).toEqual([null, null, null]);
  });

  it('related object is initialized', () => {
    stateBuilder.update([{
      ...header('ProjectSerializer', 1),
      name: 'Test project',
      tasks: [1, 2, 3],
    }]);
    stateBuilder.update([{
      ...header('TaskSerializer', 1),
      name: 'Task #1',
    }]);

    expect(stateBuilder.state!.tasks).toEqual([
      {
        id: 1,
        name: 'Task #1',
      }, null, null]);
  });

});
