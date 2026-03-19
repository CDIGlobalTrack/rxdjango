/**
 * Type-level test: verify that Saveable, Deleteable, Creatable helper types
 * compose correctly for channel-scoped state types.
 *
 * This file is NOT executed at runtime. It is checked by tsc --noEmit to
 * verify the type system knows about write methods on composed types.
 *
 * Interfaces are pure data (plain arrays). Writability is added via
 * channel-scoped type aliases using Saveable, Deleteable, Creatable.
 *
 * Payload types restrict save()/create() to only accept writable fields,
 * preventing accidental writes to read-only or unknown fields.
 */
import { ProjectType, TaskType, TaskPayload } from './StateBuilder.mock';
import { Saveable, Deleteable, Creatable } from './ContextChannel.interfaces';

// Channel-scoped state type — mirrors what makefrontend generates
type WritableTask = Saveable<Deleteable<TaskType>, TaskPayload>;
type ProjectChannelState = Omit<ProjectType, 'tasks'> & {
    tasks: Creatable<WritableTask, TaskPayload>;
};

// State from the writable channel
declare const state: ProjectChannelState;

// .create() is available on the array via Creatable, accepts payload type
const promise: Promise<number> = state.tasks.create({ taskName: 'New Task' });

// create() with no args also works (optional parameter)
const promise2: Promise<number> = state.tasks.create();

// Regular array access still works
const first: WritableTask = state.tasks[0];

// .save() and .delete() are available on instances via Saveable/Deleteable
// save() accepts the payload type (only writable fields)
const promise3: Promise<void> = first.save({ taskName: 'Updated' });
const promise4: Promise<void> = first.delete();

// save() accepts partial payload (single field)
const promise5: Promise<void> = first.save({ user: 42 });

// @ts-expect-error — payload rejects unknown fields
const badSave: Promise<void> = first.save({ nonexistent: 42 });

// @ts-expect-error — payload rejects instance meta-fields
const badSave2: Promise<void> = first.save({ id: 1 });

// @ts-expect-error — create rejects unknown fields
const badCreate: Promise<number> = state.tasks.create({ nonexistent: 42 });

// Plain interface has no write methods (read-only channel would use this)
declare const readOnlyState: ProjectType;
const plainFirst = readOnlyState.tasks[0];
// @ts-expect-error — plain TaskType has no .save()
const noSave: () => Promise<void> = plainFirst.save;
// @ts-expect-error — plain TaskType[] has no .create()
const noCreate: () => Promise<number> = readOnlyState.tasks.create;

// Suppress unused variable warnings
void promise;
void promise2;
void first;
void promise3;
void promise4;
void promise5;
void badSave;
void badSave2;
void badCreate;
void plainFirst;
void noSave;
void noCreate;
