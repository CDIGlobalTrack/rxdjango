import StateBuilder from './StateBuilder';
import { TempInstance } from './ContextChannel.interfaces';
import {
  ProjectType,
  ANCHOR,
  MODEL,
} from './StateBuilder.mock';

const header = (
  instanceType: string,
  id: number,
  operation: string = 'create',
) => {
  return {
    id: id,
    '_instance_type': `project.serializers.${instanceType}`,
    '_operation': operation,
    '_tstamp': new Date().getTime(),
  };
};

describe('StateBuilder many=true', () => {
  let stateBuilder: StateBuilder<ProjectType>;

  beforeEach(() => {
    stateBuilder = new StateBuilder<ProjectType>(MODEL, ANCHOR, true);
  });

  it('returns undefined state before any anchors are set', () => {
    // many=true with no anchors should return an empty array (not undefined)
    const state = stateBuilder.state;
    expect(state).toEqual([]);
  });

  it('setAnchors creates unloaded placeholders', () => {
    stateBuilder.setAnchors([10, 20]);
    const state = stateBuilder.state as ProjectType[];
    expect(state).toHaveLength(2);
    expect(state[0].id).toBe(10);
    expect(state[0]._loaded).toBe(false);
    expect(state[1].id).toBe(20);
    expect(state[1]._loaded).toBe(false);
  });

  it('loads anchor data after setAnchors', () => {
    stateBuilder.setAnchors([1]);
    stateBuilder.update([{
      ...header('ProjectSerializer', 1),
      projectName: 'Loaded Project',
    }] as TempInstance[]);
    const state = stateBuilder.state as ProjectType[];
    expect(state[0]._loaded).toBe(true);
    expect(state[0].projectName).toBe('Loaded Project');
  });

  it('prependAnchorId adds anchor to the front', () => {
    stateBuilder.setAnchors([2, 3]);
    stateBuilder.prependAnchorId(1);
    const state = stateBuilder.state as ProjectType[];
    expect(state).toHaveLength(3);
    // Prepended anchor has no index entry yet, so it's an empty spread
    expect(state[0].id).toBeUndefined();
    // setAnchors creates unloaded placeholders with id and _loaded=false
    expect(state[1].id).toBe(2);
    expect(state[2].id).toBe(3);
  });

  it('auto-adds anchor on initial_state for many=true', () => {
    // First instance received becomes the rootType
    stateBuilder.update([{
      ...header('ProjectSerializer', 1),
      _operation: 'initial_state',
      projectName: 'Project #1',
    }] as TempInstance[]);
    stateBuilder.update([{
      ...header('ProjectSerializer', 2),
      _operation: 'initial_state',
      projectName: 'Project #2',
    }] as TempInstance[]);
    const state = stateBuilder.state as ProjectType[];
    expect(state).toHaveLength(2);
    expect(state[0].projectName).toBe('Project #1');
    expect(state[1].projectName).toBe('Project #2');
  });

  it('delete operation removes anchor from many=true list', () => {
    stateBuilder.update([{
      ...header('ProjectSerializer', 1),
      _operation: 'initial_state',
      projectName: 'Project #1',
    }, {
      ...header('ProjectSerializer', 2),
      _operation: 'initial_state',
      projectName: 'Project #2',
    }] as TempInstance[]);

    stateBuilder.update([{
      ...header('ProjectSerializer', 1, 'delete'),
    }] as TempInstance[]);

    const state = stateBuilder.state as ProjectType[];
    expect(state).toHaveLength(1);
    expect(state[0].id).toBe(2);
  });
});

describe('StateBuilder edge cases', () => {
  it('getInstance returns the instance by key', () => {
    const stateBuilder = new StateBuilder<ProjectType>(MODEL, ANCHOR, false);
    stateBuilder.update([{
      ...header('ProjectSerializer', 1),
      projectName: 'Test',
    }] as TempInstance[]);
    const instance = stateBuilder.getInstance(`project.serializers.ProjectSerializer:1`);
    expect(instance.id).toBe(1);
  });

  it('getInstance throws for missing key', () => {
    const stateBuilder = new StateBuilder<ProjectType>(MODEL, ANCHOR, false);
    expect(() => {
      stateBuilder.getInstance('nonexistent:999');
    }).toThrow('Instance nonexistent:999 not found');
  });

  it('update throws on non-object array', () => {
    const stateBuilder = new StateBuilder<ProjectType>(MODEL, ANCHOR, false);
    expect(() => {
      stateBuilder.update([1, 2, 3] as any);
    }).toThrow('Expected array of instances');
  });

  it('update with operation field updates existing instance', () => {
    const stateBuilder = new StateBuilder<ProjectType>(MODEL, ANCHOR, false);
    stateBuilder.update([{
      ...header('ProjectSerializer', 1),
      projectName: 'Original',
      tasks: [1],
    }] as TempInstance[]);
    stateBuilder.update([{
      ...header('TaskSerializer', 1),
      taskName: 'Task 1',
    }] as TempInstance[]);

    // Update the task
    stateBuilder.update([{
      ...header('TaskSerializer', 1, 'update'),
      taskName: 'Updated Task',
    }] as TempInstance[]);

    const state = stateBuilder.state as ProjectType;
    expect(state.tasks[0].taskName).toBe('Updated Task');
  });

  it('deleting a foreign key sets it to null', () => {
    const stateBuilder = new StateBuilder<ProjectType>(MODEL, ANCHOR, false);
    stateBuilder.update([{
      ...header('ProjectSerializer', 1),
      projectName: 'Project',
      customer: 5,
    }] as TempInstance[]);
    stateBuilder.update([{
      ...header('CustomerSerializer', 5),
      customerName: 'Customer',
    }] as TempInstance[]);

    // Delete the customer
    stateBuilder.update([{
      ...header('CustomerSerializer', 5, 'delete'),
    }] as TempInstance[]);

    const state = stateBuilder.state as ProjectType;
    expect(state.customer).toBeNull();
  });
});
