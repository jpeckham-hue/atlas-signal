import unittest

from atlas_signal.urls import is_tracking_param, normalize_url


class TrackingParamTests(unittest.TestCase):
    def test_tracking_names_and_prefixes(self):
        for name in [
            "utm_source", "utm_medium", "utm_campaign", "utm_",
            "at_medium", "at_campaign", "at_link_id",
            "cmp", "fbclid", "gclid", "ocid",
        ]:
            with self.subTest(name=name):
                self.assertTrue(is_tracking_param(name))

    def test_case_insensitive(self):
        for name in ["UTM_Source", "AT_MEDIUM", "CMP", "FbClId", "GCLID", "OcId"]:
            with self.subTest(name=name):
                self.assertTrue(is_tracking_param(name))

    def test_percent_encoded_name_is_decoded_for_matching(self):
        self.assertTrue(is_tracking_param("utm%5Fsource"))

    def test_legitimate_names_kept(self):
        for name in ["id", "page", "q", "cmpid", "camp", "utm", "atom", "at", "docid", "story"]:
            with self.subTest(name=name):
                self.assertFalse(is_tracking_param(name))


class NormalizeUrlTests(unittest.TestCase):
    def assertNormalizes(self, raw, expected):
        self.assertEqual(normalize_url(raw), expected)

    # --- publisher-style tracking parameters ---

    def test_bbc_at_params_removed(self):
        self.assertNormalizes(
            "https://www.bbc.co.uk/news/articles/ck87zg8jnwngo?at_medium=RSS&at_campaign=rss",
            "https://www.bbc.co.uk/news/articles/ck87zg8jnwngo",
        )

    def test_cbc_cmp_removed(self):
        self.assertNormalizes(
            "https://www.cbc.ca/news/business/g7-trump-diesel-reserve-release-9.7366871?cmp=rss",
            "https://www.cbc.ca/news/business/g7-trump-diesel-reserve-release-9.7366871",
        )

    def test_utm_params_removed(self):
        self.assertNormalizes(
            "https://example.com/a?utm_source=x&utm_medium=email&utm_campaign=c&utm_term=t&utm_content=z",
            "https://example.com/a",
        )

    def test_click_ids_removed(self):
        self.assertNormalizes(
            "https://example.com/a?fbclid=abc&gclid=def&ocid=ghi",
            "https://example.com/a",
        )

    def test_tracking_names_case_insensitive(self):
        self.assertNormalizes(
            "https://example.com/a?UTM_Source=x&At_Medium=y&CMP=z&FBCLID=1&GClid=2&OCID=3&id=7",
            "https://example.com/a?id=7",
        )

    def test_tracking_param_without_value_removed(self):
        self.assertNormalizes("https://example.com/a?cmp&id=1", "https://example.com/a?id=1")

    # --- legitimate query parameters ---

    def test_legitimate_params_preserved_and_sorted(self):
        self.assertNormalizes(
            "https://example.com/a?page=2&id=10&utm_source=x&b=",
            "https://example.com/a?b=&id=10&page=2",
        )

    def test_sorting_is_deterministic_regardless_of_input_order(self):
        a = normalize_url("https://example.com/a?z=1&a=2&m=3")
        b = normalize_url("https://example.com/a?m=3&z=1&a=2")
        self.assertEqual(a, b)
        self.assertEqual(a, "https://example.com/a?a=2&m=3&z=1")

    def test_param_name_case_preserved(self):
        self.assertNormalizes("https://example.com/a?ID=1", "https://example.com/a?ID=1")

    def test_duplicate_params_kept_in_original_relative_order(self):
        self.assertNormalizes(
            "https://example.com/a?tag=b&x=1&tag=a",
            "https://example.com/a?tag=b&tag=a&x=1",
        )

    def test_value_only_param_kept(self):
        self.assertNormalizes("https://example.com/amp?amp", "https://example.com/amp?amp")

    def test_empty_query_segments_dropped(self):
        self.assertNormalizes("https://example.com/a?&id=1&&", "https://example.com/a?id=1")

    def test_query_removed_when_only_tracking(self):
        self.assertNormalizes("https://example.com/a?utm_source=x", "https://example.com/a")

    def test_bare_question_mark_removed(self):
        self.assertNormalizes("https://example.com/a?", "https://example.com/a")

    # --- fragment, scheme, host, port ---

    def test_fragment_removed(self):
        self.assertNormalizes("https://example.com/a#section-2", "https://example.com/a")
        self.assertNormalizes("https://example.com/a?id=1#top", "https://example.com/a?id=1")
        self.assertNormalizes("https://example.com/a#", "https://example.com/a")

    def test_http_becomes_https(self):
        self.assertNormalizes("http://example.com/a", "https://example.com/a")
        self.assertEqual(normalize_url("http://example.com/a"), normalize_url("https://example.com/a"))

    def test_scheme_case_insensitive(self):
        self.assertNormalizes("HTTP://example.com/a", "https://example.com/a")

    def test_hostname_lowercased(self):
        self.assertNormalizes("https://WWW.Example.COM/Path", "https://www.example.com/Path")

    def test_www_preserved_not_added(self):
        self.assertNormalizes("https://www.example.com/a", "https://www.example.com/a")
        self.assertNormalizes("https://example.com/a", "https://example.com/a")

    def test_default_ports_removed(self):
        self.assertNormalizes("http://example.com:80/a", "https://example.com/a")
        self.assertNormalizes("https://example.com:443/a", "https://example.com/a")

    def test_non_default_port_kept(self):
        self.assertNormalizes("https://example.com:8443/a", "https://example.com:8443/a")

    def test_ipv6_host(self):
        self.assertNormalizes("http://[2001:DB8::1]:443/a", "https://[2001:db8::1]/a")

    # --- path ---

    def test_path_preserved_exactly(self):
        self.assertNormalizes(
            "https://example.com/News/Business/Story-ABC.html",
            "https://example.com/News/Business/Story-ABC.html",
        )

    def test_trailing_slash_distinction_preserved(self):
        with_slash = normalize_url("https://example.com/news/story/")
        without = normalize_url("https://example.com/news/story")
        self.assertEqual(with_slash, "https://example.com/news/story/")
        self.assertEqual(without, "https://example.com/news/story")
        self.assertNotEqual(with_slash, without)

    def test_dot_segments_not_resolved(self):
        self.assertNormalizes("https://example.com/a/../b", "https://example.com/a/../b")

    def test_percent_encoding_preserved_in_path(self):
        self.assertNormalizes(
            "https://example.com/caf%C3%A9/a%2Fb/%7Euser",
            "https://example.com/caf%C3%A9/a%2Fb/%7Euser",
        )

    def test_percent_encoding_case_not_changed(self):
        self.assertNormalizes("https://example.com/a%2fb", "https://example.com/a%2fb")

    def test_percent_encoding_preserved_in_query(self):
        self.assertNormalizes(
            "https://example.com/search?q=canada%20eu&utm_source=x&city=Montr%C3%A9al",
            "https://example.com/search?city=Montr%C3%A9al&q=canada%20eu",
        )

    def test_plus_in_query_preserved(self):
        self.assertNormalizes("https://example.com/s?q=a+b", "https://example.com/s?q=a+b")

    def test_no_path_becomes_slash(self):
        self.assertNormalizes("https://example.com", "https://example.com/")
        self.assertNormalizes("https://example.com?id=1", "https://example.com/?id=1")
        self.assertNormalizes("https://example.com#frag", "https://example.com/")

    # --- whitespace ---

    def test_surrounding_whitespace_trimmed(self):
        self.assertNormalizes("  https://example.com/a \n\t", "https://example.com/a")

    # --- failure contract ---

    def test_invalid_schemes_rejected(self):
        for raw in [
            "ftp://example.com/a",
            "mailto:news@example.com",
            "javascript:alert(1)",
            "file:///C:/data/a.html",
            "data:text/html,hello",
            "feed://example.com/rss",
        ]:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    normalize_url(raw)

    def test_relative_urls_rejected(self):
        for raw in [
            "/news/story",
            "news/story",
            "//example.com/news",
            "www.example.com/news",
            "?id=1",
            "#frag",
        ]:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    normalize_url(raw)

    def test_malformed_input_rejected(self):
        for raw in [
            "",
            "   ",
            "not a url",
            "https://",
            "https:///path-only",
            "https:example.com/a",
            "https://example.com:notaport/a",
            "https://example.com:99999/a",
            "http://[::1/a",
            "https://example.com/a b",
            "https://example.com/a\nb",
            "https://example.com/a\x00",
        ]:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    normalize_url(raw)

    def test_credentials_rejected(self):
        for raw in ["https://user@example.com/a", "https://user:secret@example.com/a"]:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    normalize_url(raw)

    def test_non_string_rejected_with_type_error(self):
        for value in [None, 123, b"https://example.com/"]:
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    normalize_url(value)

    # --- idempotence ---

    def test_idempotent(self):
        samples = [
            "https://www.bbc.co.uk/news/articles/ck87zg8jnwngo?at_medium=RSS&at_campaign=rss",
            "http://WWW.Example.com:80/News/?b=2&a=1&utm_source=x#frag",
            "https://example.com",
            "https://example.com/caf%C3%A9?q=a+b&tag=z&tag=y",
            "https://example.com:8443/a/?amp",
            "  https://example.com/a?&&id=1  ",
            "http://[2001:DB8::1]/a",
        ]
        for raw in samples:
            with self.subTest(raw=raw):
                once = normalize_url(raw)
                self.assertEqual(normalize_url(once), once)


if __name__ == "__main__":
    unittest.main()
