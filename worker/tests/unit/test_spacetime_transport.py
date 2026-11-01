import httpx
import pytest
from tandem_worker.adapters.spacetime import StdbHttp
from tandem_worker.contracts import ContractError


def test_shared_client_keeps_owner_credentials_request_local():
    seen=[]
    def reply(request):
        seen.append(request)
        return httpx.Response(200, json={"token":"test-identity"})
    http=StdbHttp("https://db.example", "demo", transport=httpx.MockTransport(reply))
    http.call("owner-a", "submit_private_message", room_id=1, body="private")
    http.call("owner-b", "submit_private_message", room_id=1, body="different")
    http.mint_identity()
    assert [r.headers.get("authorization") for r in seen]==["Bearer owner-a","Bearer owner-b",None]
    assert seen[0].read()==b'{"roomId": 1, "body": "private"}'
    assert all(r.method=="POST" for r in seen)
    http.close()
    assert http.client.is_closed


@pytest.mark.parametrize("status,body,code",[(530,"NOT_AUTHORIZED: denied","NOT_AUTHORIZED"),(504,"timeout","OUTCOME_UNKNOWN")])
def test_contract_errors_remain_identical(status,body,code):
    http=StdbHttp("https://db.example", "demo", transport=httpx.MockTransport(lambda r:httpx.Response(status,text=body)))
    with pytest.raises(ContractError,match=code):
        http.call("owner-a","respond_to_proposal")
    http.close()


def test_ambiguous_write_is_not_retried():
    seen=[]
    def lost(request):
        seen.append(request)
        raise httpx.ReadTimeout("response lost",request=request)
    http=StdbHttp("https://db.example", "demo", transport=httpx.MockTransport(lost))
    with pytest.raises(httpx.ReadTimeout):
        http.call("owner-a","create_room")
    assert len(seen)==1
    http.close()


def test_redirect_does_not_forward_authenticated_write():
    seen=[]
    def redirect(request):
        seen.append(request)
        return httpx.Response(307,headers={"Location":"https://other.example"})
    http=StdbHttp("https://db.example", "demo", transport=httpx.MockTransport(redirect))
    with pytest.raises(RuntimeError,match="HTTP 307"):
        http.call("owner-a","create_room")
    assert len(seen)==1
    http.close()
