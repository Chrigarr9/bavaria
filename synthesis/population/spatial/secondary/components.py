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

class MiDDistanceSampler(rda.FeasibleDistanceSampler):
    """Sample trip distances from MiD 2017 Bayern empirical CDFs per activity purpose.

    Replaces ENTD-based sampling with German survey data that matches
    Bavarian travel patterns. No purpose correction factors needed.

    Source: MiD 2017 Kurzreport Bayern (Bayerisches Staatsministerium)
    CDFs: routed km converted to euclidean meters (routed / 1.3 * 1000).
    """

    # (euclidean_meters, cumulative_probability) per purpose
    _DISTANCES_M = np.array([0, 769, 1538, 3846, 7692, 15385, 38462])

    _CDFS = {
        "shop":      np.array([0, .25, .44, .72, .89, .97, 1.0]),
        "leisure":   np.array([0, .18, .30, .50, .66, .82, .95]),
        "other":     np.array([0, .28, .41, .60, .76, .88, .97]),
    }

    def __init__(self, random, maximum_iterations=1000):
        super().__init__(random=random, maximum_iterations=maximum_iterations)

    def sample_distances(self, problem):
        distances = np.zeros(len(problem["modes"]))
        for i, purpose in enumerate(problem["purposes"]):
            cdf = self._CDFS.get(purpose, self._CDFS["other"])
            u = self.random.random_sample()
            distances[i] = np.interp(u, cdf, self._DISTANCES_M)
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

    def query_ring(self, purpose, center, target_distance, tolerance=0.3, max_candidates=20):
        """Find facilities in a distance ring [target*(1-tol), target*(1+tol)] from center.

        Returns list of (identifier, location) tuples sorted by distance-to-target error.
        Returns empty list if no facilities found in the ring.
        """
        outer_radius = target_distance * (1 + tolerance)
        inner_radius = max(0, target_distance * (1 - tolerance))

        indices = self.indices[purpose].query_radius(
            center.reshape(1, -1), outer_radius
        )[0]

        if len(indices) == 0:
            return []

        candidates = []
        for idx in indices:
            loc = self.data[purpose]["locations"][idx]
            dist = la.norm(loc - center)
            if dist >= inner_radius:
                candidates.append((
                    self.data[purpose]["identifiers"][idx],
                    loc,
                    abs(dist - target_distance)
                ))

        candidates.sort(key=lambda c: c[2])
        return [(c[0], c[1]) for c in candidates[:max_candidates]]

    def sample(self, purpose, random):
        index = random.randint(0, len(self.data[purpose]["locations"]))
        identifier = self.data[purpose]["identifiers"][index]
        location = self.data[purpose]["locations"][index]
        return identifier, location

class CustomDiscretizationSolver(rda.DiscretizationSolver):
    def __init__(self, index, k_candidates = 1, use_ring_query = False):
        self.index = index
        self.k_candidates = k_candidates
        self.use_ring_query = use_ring_query

    def _ring_query_with_fallback(self, purpose, anchor, target_dist, relaxed_location):
        """Ring query with progressive tolerance widening, fallback to K-nearest."""
        for tolerance in [0.3, 0.6, 1.0]:
            candidates = self.index.query_ring(purpose, anchor, target_dist, tolerance=tolerance)
            if candidates:
                return candidates
        # Final fallback: K-nearest from relaxed location
        return self.index.query_k(purpose, relaxed_location, k=max(self.k_candidates, 10))

    def solve(self, problem, locations, target_distances = None):
        discretized_locations = []
        discretized_identifiers = []

        prev_anchor = None
        if problem["origin"] is not None:
            prev_anchor = problem["origin"].flatten()

        for i, (location, purpose) in enumerate(zip(locations, problem["purposes"])):
            has_target = (prev_anchor is not None and target_distances is not None
                          and i < len(target_distances))

            if has_target and self.use_ring_query:
                # Ring query: search facilities at target distance from anchor
                candidates = self._ring_query_with_fallback(
                    purpose, prev_anchor, target_distances[i], location
                )
                # Among ring candidates, prefer closest to relaxed location (chain direction)
                best_ident, best_loc = min(candidates, key=lambda c: la.norm(c[1] - location))

            elif has_target and self.k_candidates > 1:
                # Legacy: K-nearest from relaxed location
                candidates = self.index.query_k(purpose, location, k=self.k_candidates)
                target_dist = target_distances[i]
                best_error = np.inf
                best_ident, best_loc = candidates[0]
                for ident, loc in candidates:
                    error = abs(la.norm(loc - prev_anchor) - target_dist)
                    if error < best_error:
                        best_error = error
                        best_ident, best_loc = ident, loc
            else:
                best_ident, best_loc = self.index.query(purpose, location.reshape(1, -1))

            discretized_identifiers.append(best_ident)
            discretized_locations.append(best_loc)
            prev_anchor = best_loc

        assert len(discretized_locations) == problem["size"]

        return dict(
            valid = True, locations = np.vstack(discretized_locations),
            identifiers = discretized_identifiers
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
