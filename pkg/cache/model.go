/*
Copyright 2024 The Aibrix Team.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

	http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/
package cache

import (
	"github.com/vllm-project/aibrix/pkg/types"
	"github.com/vllm-project/aibrix/pkg/utils"
	v1 "k8s.io/api/core/v1"
)

// ModelRouterProviderFunc defines the function to provider per-model router
type ModelRouterProviderFunc func(modelName string) (types.QueueRouter, error)

type Model struct {
	// Pods is a CustomizedRegistry that stores *v1.Pod objects.
	// The internal map uses `namespace/name` as the key and `*v1.Pod` as the value.
	// This allows efficient lookups and caching of Pod objects by their unique identifier.
	// By outputing utils.PodArray on Array() call, the PodArray support efficient filtering by
	// GPU (by deployment-like names, see utils.DeploymentNameFromPod() in utils/pod.go).
	Pods *utils.CustomizedRegistry[*v1.Pod, *utils.PodArray]
	// OutputPredictor predicts the number of output tokens per request by history requests.
	// The prediction is essential to SLO-aware features.
	OutputPredictor types.OutputPredictor
	// QueueRouter maintains a local request queue, enabling flexible request reordering.
	QueueRouter types.QueueRouter

	pendingRequests int32
}
