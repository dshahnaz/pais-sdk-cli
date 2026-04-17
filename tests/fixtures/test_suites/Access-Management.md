# Access-Management

## Overview

The Access-Management test suite validates the user and role management capabilities within VMware Ops. This suite tests the creation, modification, retrieval, and deletion of user roles and object scopes within the Ops authentication and authorization system. The test suite is categorized as a functional test area (ops_func-2h) and runs on a configured one node Ops deployment with a Windows-based test driver.

## Deployment Information

- **Testbed Type**: Configured one node Ops deployment (`vrops-1slice-config-ph`)
- **Deployment Context**: Single-slice Ops environment with standard configuration
- **Test Driver**: Windows 10 with JDK 17
- **Template Deployment**: vROps template deployment enabled
- **Integrated Components**: Ops is the primary component being tested

## Components

- **Ops (vRealize Operations) - Platform core service for monitoring, analytics, and operational management**
- **VIDB (vCenter Identity Database) - Identity management and role-based access control**

## Test Coverage

### testGetAllRoles

**Purpose**: Retrieves all available roles from the Ops system and validates that the system returns the expected set of default roles.

**Validations**:
- Verifies that the getAllRoles API call succeeds
- Confirms that exactly the known number of default roles are returned
- Validates that no unexpected roles are present in the system
- Ensures all returned role names match the expected default roles

**Key Operations**:
- Calls `dataRetrieverInterface.getAllRoles()` to retrieve all roles
- Filters roles to ensure only default roles are present
- Stores all retrieved roles for use in subsequent tests

**API Endpoints**: 
- getAllRoles endpoint via the data retrieval interface

### testCreateUserRole

**Purpose**: Tests the creation of a new user-defined role with specific privileges and validates that the role is properly stored and retrievable.

**Dependencies**: Depends on `testGetAllRoles` to ensure roles are available

**Validations**:
- Confirms successful role creation via the createUserRole API
- Verifies that the newly created role appears in the role list after creation
- Validates that the role count increases by exactly one
- Confirms that the created role properties match the intended configuration
- Ensures role privileges are preserved

**Key Operations**:
- Constructs a mock role based on an existing default role
- Sets display name to "Test Role Name" and description to "Test Role Description"
- Converts privilege set to the required Privilege object format
- Calls `createUserRole()` API with role name, description, and privileges
- Retrieves all roles and performs comparison to verify creation
- Adds the new role to the in-memory role set

**API Endpoints**:
- createUserRole endpoint
- getAllRoles endpoint for verification

### testEditUserRole

**Purpose**: Tests the modification of an existing user role, specifically updating its privileges, and validates that the changes persist.

**Dependencies**: Depends on `testCreateUserRole` to ensure a user-defined role exists

**Validations**:
- Confirms successful role edit operation
- Verifies that the edited role still exists after modification
- Validates that the privilege count has changed to match the new privilege set
- Ensures only long-key privileges (length ≥ 35 characters) are retained after edit

**Key Operations**:
- Retrieves one default role for editing
- Creates a filtered privilege set by removing privileges with keys shorter than 35 characters
- Calls `editUserRole()` API with updated privileges
- Retrieves all roles post-edit to verify the changes
- Compares privilege count to ensure modifications took effect

**API Endpoints**:
- editUserRole endpoint
- getAllRoles endpoint for verification

### testDeleteUserRoles

**Purpose**: Tests the deletion of user-defined roles and validates that deleted roles are no longer accessible.

**Dependencies**: Depends on `testEditUserRole` to ensure proper role state

**Validations**:
- Confirms successful role deletion operation
- Verifies that the role count decreases by exactly one after deletion
- Ensures the deleted role is no longer present in the system
- Confirms that the role cannot be retrieved after deletion

**Key Operations**:
- Captures the initial role count
- Selects a role for deletion
- Calls `deleteUserRoles()` API with the role name
- Removes the deleted role from in-memory state
- Retrieves all roles to verify deletion and count reduction

**API Endpoints**:
- deleteUserRoles endpoint
- getAllRoles endpoint for verification

### createObjectScopeUnauthorizedTest

**Purpose**: Tests unauthorized access control when attempting to create an object scope without proper authentication.

**Status**: Currently ignored/skipped in test runs

**Validations**:
- Verifies that the createObjectScope call returns null when unauthorized
- Ensures that the system properly denies access to unauthenticated requests

**Key Operations**:
- Attempts to create an object scope without authentication
- Validates that the result is null as expected

### createObjectScopeTest

**Purpose**: Tests the creation of a new object scope with proper authentication and validates that the scope is created correctly.

**Validations**:
- Confirms successful authentication with vROps credentials
- Verifies that the object scope is created with the specified properties
- Ensures the scope name is "testScope"
- Validates that the scope description is set to "Scope used for testing"
- Confirms that traversal specifications are properly stored

**Key Operations**:
- Authenticates using provided vROps credentials
- Creates an object scope with name "testScope", description "Scope used for testing"
- Configures traversal specification for adapter instances
- Verifies successful scope creation

**API Endpoints**:
- Authentication endpoint
- createObjectScope endpoint

### getObjectScopeTest

**Purpose**: Tests retrieval and validation of an object scope that was previously created.

**Dependencies**: Depends on `createObjectScopeTest` to ensure a scope exists

**Validations**:
- Retrieves the created object scope
- Verifies scope name matches "testScope"
- Confirms scope description is preserved
- Validates traversal specification properties
- Ensures adapter kinds and propagate sets are correctly stored

**Key Operations**:
- Retrieves the object scope by name
- Compares all scope properties against expected values
- Validates adapter instance data within traversal specifications

**API Endpoints**:
- getObjectScope endpoint

### updateObjectScopeBadIdTest

**Purpose**: Tests error handling when attempting to update an object scope with an invalid ID.

**Dependencies**: Depends on `getObjectScopeTest` to establish proper state

**Validations**:
- Confirms error handling for invalid scope ID
- Verifies that error message is returned when ID is invalid
- Ensures the original scope ID is preserved

**Key Operations**:
- Temporarily sets an invalid scope ID ("bad Id")
- Attempts to create/update the scope with invalid ID
- Verifies error message is returned
- Restores the original scope ID

**API Endpoints**:
- createObjectScope endpoint (for update operation)

### updateObjectScopeTest

**Purpose**: Tests modification of an existing object scope, including name, description, and traversal specifications.

**Dependencies**: Depends on `updateObjectScopeBadIdTest` to ensure proper error handling

**Validations**:
- Confirms successful scope update operation
- Verifies that scope name changed from "testScope" to "newScope"
- Validates that scope description is updated to "new Description"
- Ensures traversal specifications list is cleared (set to empty)
- Confirms updated scope can be retrieved with new values

**Key Operations**:
- Sets new scope name to "newScope"
- Sets new description to "new Description"
- Clears traversal specs list
- Calls update operation
- Retrieves scope to verify changes

**API Endpoints**:
- updateObjectScope endpoint
- getObjectScope endpoint for verification

### deleteObjectScopeTest

**Purpose**: Tests deletion of an object scope by ID and validates that the scope is no longer accessible.

**Dependencies**: Depends on `updateObjectScopeTest` to have an updated scope

**Validations**:
- Confirms successful deletion operation
- Verifies that the scope cannot be retrieved by name after deletion
- Ensures getObjectScope returns null for deleted scope

**Key Operations**:
- Deletes the object scope using its ID
- Attempts to retrieve the scope by name
- Confirms null return value

**API Endpoints**:
- deleteObjectScope endpoint
- getObjectScope endpoint for verification

### deleteObjectScopeAgainTest

**Purpose**: Tests batch deletion of object scopes and validates error handling when attempting to delete already-deleted scopes.

**Dependencies**: Depends on `deleteObjectScopeTest` to have deleted a scope

**Validations**:
- Confirms that batch deletion operation completes successfully
- Verifies that the system handles deletion of already-deleted scopes gracefully
- Ensures result base operation completes without error

**Key Operations**:
- Calls batch deleteObjectScopes with the already-deleted scope ID
- Validates result through ErrorHandlerUtils

**API Endpoints**:
- deleteObjectScopes endpoint (batch operation)

## Technology Stack

- **Test Framework**: Java with TestNG
- **Test Driver**: Windows 10 with JDK 17
- **Test Data**: JSON configuration files for default roles
- **Logger**: Apache Log4j 2
- **Client Utilities**: Custom vROps client utilities for authentication and API interaction
- **Deployment System**: Nimbus-based test launcher with isolated testbed provisioning
