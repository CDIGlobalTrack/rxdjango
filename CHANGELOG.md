# Changelog

All notable changes to RxDjango will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Defer broadcast serialization to transaction commit time

### Fixed
- Remove question marks from TypeScript interfaces

## [0.0.43] - 2025-09-17

### Fixed
- Fix instance parent tracking
- Avoid relaying changes to channels that are not active
- Fix `ts.export_interface` not working
- Fix mongo connection before fork (fork-safe connections)

### Removed
- Remove support for "optimistic" flag in serializer

## [0.0.41] - 2025-08-19

### Added
- Prevent reconnection on manual disconnect and alert frontend with the error
- Public `disconnect()` function in ContextChannel to disconnect without reconnecting

## [0.0.40] - 2025-07-02

### Fixed
- Read-only fields can be null in TypeScript interfaces
- Fix error on `instances_list_remove`

## [0.0.39] - 2025-06-06

### Fixed
- Avoid broadcast recursion
- Fix error on `instances_list_remove`

## [0.0.38] - 2025-05-14

### Added
- Optimize delta calculations with C extension (`delta_utils_c`)

### Fixed
- Add `rxdjango/utils/__init__.py` to make it a proper module

## [0.0.37] - 2025-05-13

### Fixed
- Ghost of deleted object messes up with state construction

## [0.0.36] - 2025-04-30

### Added
- Support use of `OriginValidator` in websocket routes

### Fixed
- Do not ignore list ordering updates
- Convert Date TypeScript type to datetime when calling actions

## [0.0.35] - 2025-04-08

### Fixed
- Wait for WS connection to start before subscribing
- Fix partial instance arriving on `useChannelInstance` hook

## [0.0.34] - 2025-04-08

### Fixed
- Wait for WS connection to start before subscribing
- Fix partial instance arriving on `useChannelInstance` hook

## [0.0.33] - 2025-03-20

### Fixed
- StateBuilder now properly deletes instances
- Fix instance not being removed from channel

### Added
- GitHub Actions for testing

## [0.0.32] - 2025-03-17

### Fixed
- Allow `useChannelState` to be undefined and receive an undefined channel
- Instance not being removed from channel

### Added
- Documentation for runtime state and makefrontend

## [0.0.31] - 2025-03-12

### Added
- `getInstance` function on channel
- `subscribeInstance` function on channel

## [0.0.28] - 2025-03-07

### Added
- Progressive load of anchors

## [0.0.27] - 2025-03-07

### Fixed
- Properly declare the ContextChannel subclass when there is a RuntimeState

## [0.0.26] - 2025-03-06

### Added
- Runtime state feature (generate interface for runtime state)
- Implementation of runtime state

### Fixed
- Avoid duplicate anchors

## [0.0.25] - 2025-03-06

### Fixed
- Avoid duplicate anchors

## [0.0.24] - 2025-03-04

### Added
- New `@consumer` decorator for ContextChannel methods
- ReadTheDocs documentation setup
- Improved documentation

### Fixed
- Fix synchronization bug in heating cache state
- Set 'initial_state' operation in objects coming from mongo

## [0.0.23] - 2025-02-25

### Fixed
- Fix clear_cache for many channels
- Add `[]` before context type on channel
- Prevent TypeError when generating actions

## [0.0.22] - 2025-02-21

### Added
- Update list of anchors when instance is created or deleted
- Finish implementation of anchor list update

### Fixed
- Fix makefrontend error when generating new file
- Fix clear_cache and make it a static method

## [0.0.21] - 2025-02-12

### Fixed
- Fix loading new items in channel

## [0.0.20] - 2025-02-11

### Added
- Finish implementation of actions and many objects

## [0.0.19] - 2025-02-03

### Added
- Support for many objects in channels

## [0.0.18] - 2025-01-31

### Added
- Implement add and removal of instances in channel
- Load several instances in one channel

### Fixed
- Fixing channels to work as list

## [0.0.16] - 2024-12-06

### Fixed
- Fixing dependencies

## [0.0.15] - 2024-12-06

### Fixed
- Remove alien import

## [0.0.14] - 2024-09-27

### Added
- CLI command to broadcast a system message to all connected clients (`broadcast_system_message`)
- Reconnect persistently when receive broadcast with maintenance source

### Changed
- makefrontend command outputs diff of generated files
- Sort dependencies to avoid unnecessary changes

## [0.0.13] - 2024-07-21

### Fixed
- Fix "create"/"update" operation in websocket was swapped
- Fix instances were being broadcast prior to saving in database (data leak prevention)
- Avoid relaying same instance twice in same trigger
- Avoid not iterable error

### Added
- Raise RxDjangoBug exception to avoid data leakage

## [0.0.12] - 2024-06-20

### Added
- Implement per-channel cache cooldown
- Implement context manager to control cleanup lock

### Changed
- Renamed StateChannel to ContextChannel and "anchor" to "state"

## [0.0.11] - 2024-06-12

### Added
- `--dry-run` option on makefrontend command

### Fixed
- When a node is moved from one instance to another, children go together
- Make sure instance id is always present
- Backend sending instances with null id on delete
- Make makefrontend command explicit
- Proper simulation of a reducer
- Recursively change all parent references to trigger React

## [0.0.10] - 2024-06-06

### Fixed
- Fix error "can't read _instance of undefined"
- Rebuild whole list references
- Trigger reference change in array properties

## [0.0.9] - 2024-06-05

### Fixed
- Create new state object on every update to properly trigger React re-renders

## [0.0.8] - 2024-06-04

### Changed
- Object reference is changed in React state for proper updates

## [0.0.7] - 2024-06-04

### Changed
- Object reference is changed in React state for proper updates

## [0.0.6] - 2024-04-29

### Fixed
- Fixing InstanceType export

## [0.0.5] - 2024-04-29

### Fixed
- Fixing TypeScript exports (v0.0.4 had no build)

## [0.0.4] - 2024-04-29

### Fixed
- Fixing TypeScript exports
- Declare packages in setup.py to avoid installation error

## [0.0.3] - 2024-04-24

### Added
- Frontend is generated automatically on runserver
- Set and check mtime of TypeScript files and only generate when changes occur
- Add no connection listener

### Fixed
- Use @rxdjango/react on interfaces too
- Change state reference to trigger React
- Avoid re-generating frontend files if nothing has changed
- Avoid sending empty update to frontend

## [0.0.2] - 2024-04-05

### Added
- Send incremental updates to frontend
- Support for delete operation in StateChannel.broadcast_instance

### Fixed
- Consider delete and exceptional case in incremental updates

## [0.0.1] - 2024-02-14

### Added
- Initial support for GridFS in Mongo cache for large documents
- Support for ForwardManyToOne relation and reverse accessor on related properties

### Fixed
- Fix broadcast instance to user
- Fix user key functionality
- Fix parent not updated on client when new instance is created
- Serialized must have _user_key

## [0.0.0] - 2023-10-09

### Added
- Initial public release of RxDjango
- StateChannel concept for real-time state synchronization
- TypeScript code generation from Django serializers
- WebSocket-based communication via Django Channels
- MongoDB caching layer
- Redis coordination layer
- React integration with StateBuilder
- PersistentWebsocket for connection management
- Support for nested serializers
- ManyToMany and ForeignKey relationship handling
- Authentication via Django REST Framework tokens
- `makefrontend` management command

---

## Pre-release History (2022-2023)

### 2023-09-20 - 2023-10-09
- Major rewrite with new StateChannel concept
- Migration from wsframework to rxdjango
- Frontend library development
- PersistentWebsocket implementation
- StateBuilder for client-side state management

### 2022-10-23
- Initial repository creation

[Unreleased]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.43...HEAD
[0.0.43]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.37...v0.0.43
[0.0.41]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.37...v0.0.41
[0.0.40]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.37...v0.0.40
[0.0.39]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.37...v0.0.39
[0.0.38]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.37...v0.0.38
[0.0.37]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.35...v0.0.37
[0.0.36]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.35...v0.0.36
[0.0.35]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.34...v0.0.35
[0.0.34]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.33...v0.0.34
[0.0.33]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.32...v0.0.33
[0.0.32]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.31...v0.0.32
[0.0.31]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.28...v0.0.31
[0.0.28]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.27...v0.0.28
[0.0.27]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.26...v0.0.27
[0.0.26]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.25...v0.0.26
[0.0.25]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.24...v0.0.25
[0.0.24]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.15...v0.0.24
[0.0.23]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.15...v0.0.23
[0.0.22]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.15...v0.0.22
[0.0.21]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.15...v0.0.21
[0.0.20]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.15...v0.0.20
[0.0.19]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.15...v0.0.19
[0.0.18]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.15...v0.0.18
[0.0.16]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.15...v0.0.16
[0.0.15]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.14...v0.0.15
[0.0.14]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.13...v0.0.14
[0.0.13]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.12...v0.0.13
[0.0.12]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.11...v0.0.12
[0.0.11]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.10...v0.0.11
[0.0.10]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.9...v0.0.10
[0.0.9]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.8...v0.0.9
[0.0.8]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.7...v0.0.8
[0.0.7]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.6...v0.0.7
[0.0.6]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.5...v0.0.6
[0.0.5]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.4...v0.0.5
[0.0.4]: https://github.com/CDIGlobalTrack/rxdjango/compare/v0.0.3...v0.0.4
[0.0.3]: https://github.com/CDIGlobalTrack/rxdjango/releases/tag/v0.0.3
