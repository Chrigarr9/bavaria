import synthesis.population.spatial.secondary.rda as rda
import sklearn.neighbors
import numpy as np
import numpy.linalg as la

class CustomDistanceSampler(rda.FeasibleDistanceSampler):
    def __init__(self, random, distributions, maximum_iterations = 1000,
                 leisure_correction_factor = 1.0, shop_correction_factor = 1.0,
                 other_correction_factor = 1.0):
        rda.FeasibleDistanceSampler.__init__(self, random = random, maximum_iterations = maximum_iterations)

        self.random = random
        self.distributions = distributions
        self.purpose_correction_factors = {
            "leisure": leisure_correction_factor,
            "shop": shop_correction_factor,
            "other": other_correction_factor,
        }

    def sample_distances(self, problem):
        distances = np.zeros((len(problem["modes"])))

        for index, (mode, travel_time, purpose) in enumerate(zip(problem["modes"], problem["travel_times"], problem["purposes"])):
            mode_distribution = self.distributions[mode]

            bound_index = np.count_nonzero(travel_time > mode_distribution["bounds"])
            mode_distribution = mode_distribution["distributions"][bound_index]

            distances[index] = mode_distribution["values"][
                np.count_nonzero(self.random.random_sample() > mode_distribution["cdf"])
            ]

            if purpose in self.purpose_correction_factors:
                distances[index] *= self.purpose_correction_factors[purpose]

        return distances

class CandidateIndex:
    def __init__(self, data):
        self.data = data
        self.indices = {}

        for purpose, data in self.data.items():
            print("Constructing spatial index for %s ..." % purpose)
            self.indices[purpose] = sklearn.neighbors.KDTree(data["locations"])

    def query(self, purpose, location):
        index = self.indices[purpose].query(location.reshape(1, -1), return_distance = False)[0][0]
        identifier = self.data[purpose]["identifiers"][index]
        location = self.data[purpose]["locations"][index]
        return identifier, location

    def query_k(self, purpose, location, k = 5):
        """Return K nearest candidates as list of (identifier, location) tuples."""
        n_available = len(self.data[purpose]["locations"])
        k = min(k, n_available)
        distances, indices = self.indices[purpose].query(location.reshape(1, -1), k = k, return_distance = True)
        candidates = []
        for i in range(k):
            idx = indices[0][i]
            candidates.append((
                self.data[purpose]["identifiers"][idx],
                self.data[purpose]["locations"][idx]
            ))
        return candidates

    def sample(self, purpose, random):
        index = random.randint(0, len(self.data[purpose]["locations"]))
        identifier = self.data[purpose]["identifiers"][index]
        location = self.data[purpose]["locations"][index]
        return identifier, location

class CustomDiscretizationSolver(rda.DiscretizationSolver):
    def __init__(self, index, k_candidates = 1):
        self.index = index
        self.k_candidates = k_candidates

    def solve(self, problem, locations, target_distances = None):
        discretized_locations = []
        discretized_identifiers = []

        # Build chain of anchor points for distance-aware selection
        prev_anchor = None
        if problem["origin"] is not None:
            prev_anchor = problem["origin"].flatten()

        for i, (location, purpose) in enumerate(zip(locations, problem["purposes"])):
            if self.k_candidates > 1 and prev_anchor is not None and target_distances is not None and i < len(target_distances):
                # Query K nearest candidates and pick the one that best
                # preserves the target distance from the previous chain point
                candidates = self.index.query_k(purpose, location, k = self.k_candidates)
                target_dist = target_distances[i]

                best_error = np.inf
                best_ident, best_loc = candidates[0]
                for ident, loc in candidates:
                    actual_dist = la.norm(loc - prev_anchor)
                    error = abs(actual_dist - target_dist)
                    if error < best_error:
                        best_error = error
                        best_ident, best_loc = ident, loc

                discretized_identifiers.append(best_ident)
                discretized_locations.append(best_loc)
                prev_anchor = best_loc
            else:
                identifier, loc = self.index.query(purpose, location.reshape(1, -1))
                discretized_identifiers.append(identifier)
                discretized_locations.append(loc)
                prev_anchor = loc

        assert len(discretized_locations) == problem["size"]

        return dict(
            valid = True, locations = np.vstack(discretized_locations), identifiers = discretized_identifiers
        )

class CustomFreeChainSolver(rda.RelaxationSolver):
    def __init__(self, random, index):
        self.random = random
        self.index = index

    def solve(self, problem, distances):
        identifier, anchor = self.index.sample(problem["purposes"][0], self.random)
        locations = rda.sample_tail(self.random, anchor, distances)
        locations = np.vstack((anchor, locations))

        assert len(locations) == len(distances) + 1
        return dict(valid = True, locations = locations)
