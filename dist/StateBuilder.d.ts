import { InstanceType, Model, TempInstance } from './ContextChannel.interfaces';
/**
 * Reconstructs nested state from flat server instances.
 *
 * The backend sends instances in a flat format with `_instance_type` fields.
 * StateBuilder maintains an instance registry and rebuilds the nested
 * structure expected by React components. It simulates reducer behavior:
 * on every instance update, all references to that instance are changed,
 * and parent references are recursively updated to trigger React re-renders.
 *
 * @template T - The type of the root/anchor instance
 *
 * @example
 * ```typescript
 * const builder = new StateBuilder<Project>(model, 'myapp.serializers.ProjectSerializer', false);
 *
 * // Handle incoming instances from server
 * builder.update([
 *   { id: 1, _instance_type: 'myapp.serializers.ProjectSerializer', name: 'Project 1', ... },
 *   { id: 1, _instance_type: 'myapp.serializers.TaskSerializer', title: 'Task 1', ... }
 * ]);
 *
 * // Get rebuilt nested state
 * const state = builder.state;
 * // { id: 1, name: 'Project 1', tasks: [{ id: 1, title: 'Task 1' }] }
 * ```
 */
export default class StateBuilder<T> {
    /** The model definition mapping instance types to their relational fields. */
    private model;
    /** The `_instance_type` string of the root/anchor serializer. */
    private anchor;
    private rootType;
    /** Ordered list of anchor instance IDs. */
    private anchorIds;
    /** Index of all instances by `_instance_type:id` key. */
    private index;
    /** Tracks all references to each instance for recursive change propagation. */
    private refs;
    /** Tracks which anchor IDs are loaded to avoid duplication. */
    private anchorIndex;
    /** Whether this channel uses many=True (list of anchors). */
    private many;
    /**
     * @param model - The model definition from the generated channel
     * @param anchor - The `_instance_type` of the root serializer
     * @param many - Whether the channel state is a list of anchors
     */
    constructor(model: Model, anchor: string, many: boolean);
    /** Prepend an anchor ID to the front of the list (for newly added instances). */
    prependAnchorId(anchorId: number): void;
    /**
     * Returns the current rebuilt nested state.
     * Each call returns a new reference to trigger React re-renders.
     *
     * @returns The nested state object (single or array), or undefined if no anchors loaded.
     */
    get state(): T | T[] | undefined;
    /**
     * Process a batch of instance updates from the server.
     *
     * @param instances - Array of flat instances with `_instance_type` and `_operation` fields
     */
    update(instances: TempInstance[] | number[]): void;
    /**
     * Get an instance by its key.
     *
     * @param key - Instance key in format `_instance_type:id`
     * @returns The instance object
     * @throws Error if instance not found
     */
    getInstance(key: string): InstanceType;
    private setAnchor;
    /**
     * Set the initial anchor IDs received from the server.
     * Creates placeholder (unloaded) instances for each anchor.
     */
    setAnchors(instanceIds: number[]): void;
    private receiveInstance;
    private deleteInstance;
    private changeRef;
    private getOrCreate;
}
